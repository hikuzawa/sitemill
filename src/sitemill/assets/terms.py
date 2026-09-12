"""自治体・観光協会の素材ページのライセンス判定（ADR 0020）。

規約ページを取得し、既存のホワイトリスト判定（`license.detector`）にかける。
明示的な CC BY / CC0 / 政府標準利用規約 2.0 の表記がある場合だけ採用する。
「申請が必要」「商用利用は要相談」のようなページは、規約として読めても**不採用**にする。
利用の条件が人間の判断を要するものは、自動で回す仕組みに載せられない。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sitemill.license.detector import detect_license
from sitemill.models import LicenseVerdict

log = logging.getLogger(__name__)

# 人の判断を要する条件。ホワイトリストに一致しても、これがあれば不採用にする。
NEEDS_PERMISSION = re.compile(
    r"申請|許可を得|承認を得|要相談|お問い合わせのうえ|事前に連絡|使用許可|利用許可|"
    r"届出|審査|個別に契約"
)


@dataclass
class TermsVerdict:
    """素材ページの判定結果。"""

    url: str
    verdict: LicenseVerdict
    needs_permission: str | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict.allowed and self.needs_permission is None

    @property
    def reason(self) -> str:
        if self.needs_permission:
            return f"利用に人の判断が必要（「{self.needs_permission}」の記載）"
        return self.verdict.reason


def read_terms(html: str, url: str, *, credit_name: str) -> TermsVerdict:
    verdict = detect_license(html, url, credit_name=credit_name)
    hit = NEEDS_PERMISSION.search(html)
    return TermsVerdict(url=url, verdict=verdict, needs_permission=hit.group(0) if hit else None)


def fetch_terms(
    client: Any, url: str, *, credit_name: str
) -> tuple[TermsVerdict | None, str | None]:
    """規約ページを取得して判定する。返り値は (結果, 失敗理由)。"""
    res = client.get(url)
    if not res.ok:
        return None, f"規約ページを取得できない（status={res.status} {res.error or ''}）"
    return read_terms(res.text, url, credit_name=credit_name), None
