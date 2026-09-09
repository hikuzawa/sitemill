"""ライセンス判定の結果。既定は不採用（ADR 0005）。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel


class LicenseId(StrEnum):
    """ホワイトリストに載るライセンス識別子。ここに無いものは採用しない。"""

    CC_BY_4_0 = "CC-BY-4.0"
    CC_BY_3_0 = "CC-BY-3.0"
    CC_BY_2_1_JP = "CC-BY-2.1-JP"
    CC0_1_0 = "CC0-1.0"
    GSTU_2_0 = "GSTU-2.0"
    MUNICIPAL_OPENDATA_CC_BY = "MUNICIPAL-OPENDATA-CC-BY"


WHITELIST: frozenset[LicenseId] = frozenset(LicenseId)

LICENSE_LABELS: dict[LicenseId, str] = {
    LicenseId.CC_BY_4_0: "クリエイティブ・コモンズ 表示 4.0 国際（CC BY 4.0）",
    LicenseId.CC_BY_3_0: "クリエイティブ・コモンズ 表示 3.0（CC BY 3.0）",
    LicenseId.CC_BY_2_1_JP: "クリエイティブ・コモンズ 表示 2.1 日本（CC BY 2.1 JP）",
    LicenseId.CC0_1_0: "CC0 1.0（パブリックドメイン提供）",
    LicenseId.GSTU_2_0: "政府標準利用規約（第2.0版）",
    LicenseId.MUNICIPAL_OPENDATA_CC_BY: "自治体オープンデータ利用規約（CC BY 互換と明記）",
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
