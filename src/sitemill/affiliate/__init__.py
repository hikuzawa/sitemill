"""ASP の案件を選ぶ（ADR 0019）。

貼り付けた検索結果 → 構造化（`parse`）→ 判定と並べ替え（`screen`）→ 表（`report`）→
承認後の受け渡し（`emit`）。判定の物差しはサービス固有なので `profile` の YAML で外から渡す。
"""

from sitemill.affiliate.emit import EmitError, render_code, render_handoff, suggest_offer_id
from sitemill.affiliate.models import Candidate, Screened, Verdict
from sitemill.affiliate.parse import parse_offers
from sitemill.affiliate.profile import Profile, ProfileError, load_profile
from sitemill.affiliate.report import markdown_report
from sitemill.affiliate.screen import ScreenResult, screen

__all__ = [
    "Candidate",
    "EmitError",
    "Profile",
    "ProfileError",
    "ScreenResult",
    "Screened",
    "Verdict",
    "load_profile",
    "markdown_report",
    "parse_offers",
    "render_code",
    "render_handoff",
    "screen",
    "suggest_offer_id",
]
