"""Search Console API の呼び出し（ADR 0023）。

サービスアカウントの鍵で JWT を作り、アクセストークンに交換して 3 つの API を叩く。

- `searchAnalytics/query` … 検索パフォーマンス（検索語・ページ・表示・クリック・CTR・順位）
- `sitemaps` … 送信したサイトマップの状態（送信 URL 数・最終取得・エラー）
- `urlInspection/index:inspect` … 1 URL のインデックス状態

鍵は `.env` の `GOOGLE_SEARCH_CONSOLE_KEY`（サービスアカウントの JSON を base64 にしたもの、
または JSON そのもの）だけから読む。他の場所は探索しない。

読み取り専用のスコープ（`webmasters.readonly`）しか要求しない。データの取得だけで、
Search Console 側の設定は変えない。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from sitemill.settings import SecretsError

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://searchconsole.googleapis.com"
# 1 回のクエリで受け取る行数の上限。API の上限は 25,000
ROW_LIMIT = 5000
# URL 検査の 1 日あたりの上限（Google の割り当ては 1 プロパティ 2,000 件/日、600 件/分）。
# 余裕を残し、既定では 1 日 200 件ずつ順に回す
INSPECT_PER_DAY = 200


class SearchConsoleError(RuntimeError):
    """API が失敗した。理由（HTTP と本文の先頭）をそのまま持つ。"""


@dataclass(frozen=True)
class Row:
    """検索パフォーマンスの 1 行。値は API が返したまま（こちらで丸めない）。"""

    keys: tuple[str, ...]
    clicks: int
    impressions: int
    ctr: float
    position: float


def load_service_account(raw: str | None) -> dict[str, Any]:
    """`.env` の値をサービスアカウントの JSON にする。base64 でも JSON そのものでもよい。"""
    if not raw:
        raise SecretsError(
            "Search Console の鍵がありません。.env に GOOGLE_SEARCH_CONSOLE_KEY を書いてください"
            "（サービスアカウントの JSON を base64 にした 1 行）"
        )
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise SecretsError(
                "GOOGLE_SEARCH_CONSOLE_KEY を読めません（base64 か JSON を入れてください）"
            ) from e
    try:
        info = json.loads(text)
    except json.JSONDecodeError as e:
        raise SecretsError("GOOGLE_SEARCH_CONSOLE_KEY が JSON ではありません") from e
    missing = [k for k in ("client_email", "private_key", "token_uri") if k not in info]
    if missing:
        raise SecretsError(f"サービスアカウントの鍵に項目がありません: {', '.join(missing)}")
    return info


def _assertion(info: dict[str, Any]) -> str:
    """サービスアカウントの鍵で署名した JWT。google-auth は署名にだけ使う。"""
    from google.auth import crypt, jwt

    signer = crypt.RSASigner.from_service_account_info(info)
    now = int(time.time())
    payload = {
        "iss": info["client_email"],
        "scope": SCOPE,
        "aud": info.get("token_uri", TOKEN_URI),
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(signer, payload).decode("utf-8")


class SearchConsole:
    """1 プロパティ分の API。トークンは期限まで使い回す。"""

    def __init__(self, info: dict[str, Any], property_url: str, *, timeout: float = 60.0) -> None:
        self.info = info
        self.property_url = property_url
        self._client = httpx.Client(timeout=timeout)
        self._token = ""
        self._expires = 0.0
        self.request_count = 0

    def __enter__(self) -> SearchConsole:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def account(self) -> str:
        return str(self.info.get("client_email", ""))

    def _headers(self) -> dict[str, str]:
        if not self._token or time.time() > self._expires - 60:
            res = self._client.post(
                self.info.get("token_uri", TOKEN_URI),
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": _assertion(self.info),
                },
            )
            if res.status_code != 200:
                raise SearchConsoleError(
                    f"トークンを取得できない: {res.status_code} {res.text[:200]}"
                )
            payload = res.json()
            self._token = payload["access_token"]
            self._expires = time.time() + float(payload.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_count += 1
        res = self._client.request(method, url, headers=self._headers(), json=body)
        if res.status_code != 200:
            raise SearchConsoleError(
                f"{method} {url.split('/')[-1]}: {res.status_code} {res.text[:300]}"
            )
        return res.json()

    @property
    def _site(self) -> str:
        return quote(self.property_url, safe="")

    def properties(self) -> list[tuple[str, str]]:
        """このサービスアカウントが見られるプロパティ。(識別子, 権限) の一覧。"""
        data = self._call("GET", f"{API}/webmasters/v3/sites")
        return [(e["siteUrl"], e.get("permissionLevel", "")) for e in data.get("siteEntry", [])]

    def performance(
        self, start: date, end: date, dimensions: tuple[str, ...], *, row_limit: int = ROW_LIMIT
    ) -> tuple[list[Row], bool]:
        """検索パフォーマンス。返り値は (行, 打ち切られたか)。

        打ち切りは記録する。「0 件」と「取りきれていない」を混ぜない。
        """
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": list(dimensions),
            "rowLimit": row_limit,
            "dataState": "all",  # 確定前の直近の日も受け取る（後日の取り直しで上書きする）
        }
        data = self._call(
            "POST", f"{API}/webmasters/v3/sites/{self._site}/searchAnalytics/query", body
        )
        rows = [
            Row(
                keys=tuple(r.get("keys", ())),
                clicks=int(r.get("clicks", 0)),
                impressions=int(r.get("impressions", 0)),
                ctr=float(r.get("ctr", 0.0)),
                position=float(r.get("position", 0.0)),
            )
            for r in data.get("rows", [])
        ]
        return rows, len(rows) >= row_limit

    def sitemaps(self) -> list[dict[str, Any]]:
        """送信済みサイトマップの状態。

        `contents[].indexed` は Google が値を返さなくなって久しく、常に 0 である。
        **インデックス数として読まない**（数えるのは URL 検査の方。ADR 0023）。
        """
        data = self._call("GET", f"{API}/webmasters/v3/sites/{self._site}/sitemaps")
        return list(data.get("sitemap", []))

    def inspect(self, url: str) -> dict[str, Any]:
        """1 URL のインデックス状態。"""
        body = {"inspectionUrl": url, "siteUrl": self.property_url}
        data = self._call("POST", f"{API}/v1/urlInspection/index:inspect", body)
        return dict(data.get("inspectionResult", {}))
