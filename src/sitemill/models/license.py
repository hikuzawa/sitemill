"""ライセンス判定の結果。既定は不採用（ADR 0005）。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel


class LicenseId(StrEnum):
    """ホワイトリストに載るライセンス識別子。ここに無いものは採用しない。"""

    CC_BY_4_0 = "CC-BY-4.0"
    CC_BY_3_0 = "CC-BY-3.0"
    CC_BY_2_5 = "CC-BY-2.5"
    CC_BY_2_0 = "CC-BY-2.0"
    CC_BY_2_1_JP = "CC-BY-2.1-JP"
    # 継承（ShareAlike）つき。2026-09-14 にホワイトリストへ入れた（下の注記）
    CC_BY_SA_4_0 = "CC-BY-SA-4.0"
    CC_BY_SA_3_0 = "CC-BY-SA-3.0"
    CC_BY_SA_2_5 = "CC-BY-SA-2.5"
    CC_BY_SA_2_0 = "CC-BY-SA-2.0"
    CC_BY_SA_2_1_JP = "CC-BY-SA-2.1-JP"
    CC0_1_0 = "CC0-1.0"
    PD_MARK_1_0 = "PD-Mark-1.0"
    PUBLIC_DOMAIN = "public-domain"
    GSTU_2_0 = "GSTU-2.0"
    MUNICIPAL_OPENDATA_CC_BY = "MUNICIPAL-OPENDATA-CC-BY"


WHITELIST: frozenset[LicenseId] = frozenset(LicenseId)

# 継承（ShareAlike）つきのライセンス。
#
# 2026-09-12 の時点では「受け入れるとサイトの該当部分に取り消せない義務を負う」と読んで
# 外していたが、**継承の義務はその写真とその改変物に及ぶもので、写真を載せたページには
# 及ばない**（ページは編集物であって二次的著作物ではない）。2026-09-14 に入れ直した。
#
# 代わりに守ること（sitemill ADR 0005 追記）:
#   - 写真を**改変しない**。表示のための縮小はしてよい（Commons のサムネイル）が、
#     切り抜き・加工はしない
#   - クレジットに**作者名・ライセンス名・出典ページへのリンク**を必ず出す
#     （`photo_figure` マクロが 3 つとも出す。リンク先の Commons のファイルページで
#     原文のライセンスが確認できる）
SHARE_ALIKE: frozenset[LicenseId] = frozenset(
    {
        LicenseId.CC_BY_SA_4_0,
        LicenseId.CC_BY_SA_3_0,
        LicenseId.CC_BY_SA_2_5,
        LicenseId.CC_BY_SA_2_0,
        LicenseId.CC_BY_SA_2_1_JP,
    }
)
# 継承の義務が生じないもの。継承を避けたいサービスはこちらを使う
NO_SHARE_ALIKE: frozenset[LicenseId] = frozenset(LicenseId) - SHARE_ALIKE

LICENSE_LABELS: dict[LicenseId, str] = {
    LicenseId.CC_BY_4_0: "クリエイティブ・コモンズ 表示 4.0 国際（CC BY 4.0）",
    LicenseId.CC_BY_3_0: "クリエイティブ・コモンズ 表示 3.0（CC BY 3.0）",
    LicenseId.CC_BY_2_5: "クリエイティブ・コモンズ 表示 2.5（CC BY 2.5）",
    LicenseId.CC_BY_2_0: "クリエイティブ・コモンズ 表示 2.0（CC BY 2.0）",
    LicenseId.CC_BY_2_1_JP: "クリエイティブ・コモンズ 表示 2.1 日本（CC BY 2.1 JP）",
    LicenseId.CC_BY_SA_4_0: "クリエイティブ・コモンズ 表示-継承 4.0 国際（CC BY-SA 4.0）",
    LicenseId.CC_BY_SA_3_0: "クリエイティブ・コモンズ 表示-継承 3.0（CC BY-SA 3.0）",
    LicenseId.CC_BY_SA_2_5: "クリエイティブ・コモンズ 表示-継承 2.5（CC BY-SA 2.5）",
    LicenseId.CC_BY_SA_2_0: "クリエイティブ・コモンズ 表示-継承 2.0（CC BY-SA 2.0）",
    LicenseId.CC_BY_SA_2_1_JP: "クリエイティブ・コモンズ 表示-継承 2.1 日本（CC BY-SA 2.1 JP）",
    LicenseId.CC0_1_0: "CC0 1.0（パブリックドメイン提供）",
    LicenseId.PD_MARK_1_0: "パブリックドメイン・マーク 1.0",
    LicenseId.PUBLIC_DOMAIN: "パブリックドメイン（著作権の保護期間が満了・権利者が放棄）",
    LicenseId.GSTU_2_0: "政府標準利用規約（第2.0版）",
    LicenseId.MUNICIPAL_OPENDATA_CC_BY: "自治体オープンデータ利用規約（CC BY 互換と明記）",
}


# 言語に依らない短い表記。多言語ページではこちらを出し、説明はカタログの文言で補う。
LICENSE_SHORT: dict[LicenseId, str] = {
    LicenseId.CC_BY_4_0: "CC BY 4.0",
    LicenseId.CC_BY_3_0: "CC BY 3.0",
    LicenseId.CC_BY_2_5: "CC BY 2.5",
    LicenseId.CC_BY_2_0: "CC BY 2.0",
    LicenseId.CC_BY_2_1_JP: "CC BY 2.1 JP",
    LicenseId.CC_BY_SA_4_0: "CC BY-SA 4.0",
    LicenseId.CC_BY_SA_3_0: "CC BY-SA 3.0",
    LicenseId.CC_BY_SA_2_5: "CC BY-SA 2.5",
    LicenseId.CC_BY_SA_2_0: "CC BY-SA 2.0",
    LicenseId.CC_BY_SA_2_1_JP: "CC BY-SA 2.1 JP",
    LicenseId.CC0_1_0: "CC0 1.0",
    LicenseId.PD_MARK_1_0: "Public Domain Mark 1.0",
    LicenseId.PUBLIC_DOMAIN: "Public domain",
    LicenseId.GSTU_2_0: "GSTU 2.0",
    LicenseId.MUNICIPAL_OPENDATA_CC_BY: "CC BY (municipal open data)",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class LicenseVerdict(BaseModel):
    allowed: bool = False
    license_id: LicenseId | None = None
    evidence_url: str | None = None
    evidence_text: str | None = None
    credit_text: str | None = None
    checked_at: datetime
    reason: str

    @classmethod
    def denied(
        cls, reason: str, *, url: str | None = None, checked_at: datetime | None = None
    ) -> LicenseVerdict:
        return cls(allowed=False, evidence_url=url, checked_at=checked_at or _now(), reason=reason)

    @classmethod
    def granted(
        cls,
        license_id: LicenseId,
        *,
        evidence_url: str,
        evidence_text: str,
        credit_text: str,
        checked_at: datetime | None = None,
    ) -> LicenseVerdict:
        return cls(
            allowed=True,
            license_id=license_id,
            evidence_url=evidence_url,
            evidence_text=evidence_text,
            credit_text=credit_text,
            checked_at=checked_at or _now(),
            reason=f"whitelist:{license_id.value}",
        )

    @property
    def label(self) -> str:
        return LICENSE_LABELS[self.license_id] if self.license_id else "ライセンス未確認（不採用）"

    @property
    def short_label(self) -> str:
        """言語に依らない表記（CC BY 4.0 など）。多言語ページのクレジットに使う。"""
        return LICENSE_SHORT[self.license_id] if self.license_id else ""
