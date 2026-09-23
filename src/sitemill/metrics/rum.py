"""Cloudflare Web Analytics（RUM）の表示数を読む（ADR 0007 追記、2026-09-23）。

転送ページのように「開かれた回数」を数えたいページの表示数を、Cloudflare の GraphQL API
（`rumPageloadEventsAdaptiveGroups`）から取る。返すのは「パス → 表示数」の辞書だけで、
どのパスを何の名前で並べるかはサービス側で決める。

必要なもの（`.env` / CI の Secrets）:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN` に **Account Analytics: Read** の権限（配置用の権限だけでは 403 になる）

**絞り込みは `siteTag` ではなくホスト名（`requestHost`）で行う。** 同じホスト名で Web Analytics
の登録が 2 つあると、HTML に挿し込まれている `data-cf-beacon` の token の側にイベントが 1 件も
入らないことがある（japan-open-today で 2026-09-23 に実際に起きた）。ホスト名で絞れば、どちらの
登録に入っていても取れる。

**落とし穴**: ビーコンは Cloudflare が応答に自動で挿し込む形にできるが、挿し込まれるのは
**ブラウザのナビゲーションと同じ形の要求**にだけ。素の `fetch()` や `curl` で取った HTML には
入らないので、「HTML に `data-cf-beacon` があるか」で計測の有無を判定すると必ず「無い」と出る。
`Accept: text/html` と `Sec-Fetch-Mode: navigate`（`NAVIGATION_HEADERS`）を付けて取れば入る。
利用者側（UA）は関係ない。同じ理由で、そうやって開いた分は表示数にも出ない。

鍵・権限・通信のいずれかが欠ければ `None` を返す（週次の集計を止めない）。何を足せばよいかは
`NEED` に書いてあるので、そのまま報告に載せられる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:  # pragma: no cover - 型のためだけ（settings との循環を避ける）
    from sitemill.settings import Secrets

ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"

QUERY = """
query($account: string!, $host: string!, $since: Time!, $until: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      rumPageloadEventsAdaptiveGroups(
        filter: {requestHost: $host, datetime_geq: $since, datetime_leq: $until}
        limit: 500
        orderBy: [count_DESC]
      ) {
        count
        dimensions { requestPath }
      }
    }
  }
}
"""

NEED = (
    "- クリック数は出せなかった。`CLOUDFLARE_API_TOKEN` に **Account Analytics: Read** が要る"
    "（配置用の権限だけでは読めない）。権限を足したトークンと、CI・手元の値が同じかも見る。"
    "当面は Cloudflare の Web Analytics の画面で `/go/` のページ別表示数を見る"
)

# 自動挿入のビーコンが入る形の要求。計測の有無をスクリプトで確かめるときに付ける。
NAVIGATION_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Sec-Fetch-Mode": "navigate",
}


def rum_pageloads(
    secrets: Secrets, host: str, days: int = 7, *, timeout: float = 60
) -> dict[str, int] | None:
    """`host` の直近 `days` 日の表示数を「パス → 回数」で返す。取れなければ None。"""
    token = secrets.cloudflare_api_token
    account = secrets.cloudflare_account_id
    if not (token and account and host):
        return None
    until = datetime.now(UTC).replace(microsecond=0)
    since = until - timedelta(days=days)
    try:
        resp = httpx.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "query": QUERY,
                "variables": {
                    "account": account,
                    "host": host,
                    "since": _stamp(since),
                    "until": _stamp(until),
                },
            },
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if body.get("errors") or not (body.get("data") or {}).get("viewer"):
        return None
    accounts = body["data"]["viewer"].get("accounts") or []
    if not accounts:
        return None
    return {
        row["dimensions"]["requestPath"]: row["count"]
        for row in accounts[0].get("rumPageloadEventsAdaptiveGroups") or []
    }


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")
