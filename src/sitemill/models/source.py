"""巡回対象（Source）の定義。サービス側の YAML から読み込む。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from sitemill.models.license import LicenseVerdict


class CrawlPolicy(StrEnum):
    crawl = "crawl"
    link_only = "link_only"


class OperatorKind(StrEnum):
    municipality = "municipality"
    municipality_affiliated = "municipality_affiliated"
    third_party = "third_party"
    unknown = "unknown"


CRAWLABLE_OPERATORS = frozenset({OperatorKind.municipality, OperatorKind.municipality_affiliated})


class PageKind(StrEnum):
    listing_index = "listing_index"
    listing_detail = "listing_detail"
    subsidy = "subsidy"
    info = "info"
    other = "other"


class FollowRule(BaseModel):
    """seed ページから辿るリンクの規則。pattern は絶対 URL に対する正規表現。"""

    pattern: str
    kind: PageKind = PageKind.listing_detail
    max_links: int | None = None


class SeedPage(BaseModel):
    url: str
    kind: PageKind = PageKind.info
    follow: list[FollowRule] = Field(default_factory=list)


class OperatorEvidence(BaseModel):
    """運営主体を示す根拠（ページ上の記述の引用と URL）。"""

    quote: str
    url: str
    checked_on: date | None = None


class ExternalLink(BaseModel):
    """巡回しないが案内はする外部リンク（民間プラットフォームなど）。"""

    label: str
    url: str
    note: str | None = None


class Source(BaseModel):
    id: str
    name: str
    operator: str
    operator_kind: OperatorKind = OperatorKind.unknown
    operator_evidence: OperatorEvidence | None = None
    policy: CrawlPolicy = CrawlPolicy.link_only
    official_url: str
    pages: list[SeedPage] = Field(default_factory=list)
    allow_hosts: list[str] = Field(default_factory=list)
    content_selector: str | None = None
    ignore_patterns: list[str] = Field(default_factory=list)
    delay_seconds: float | None = None
    max_pages: int = 30
    license: LicenseVerdict | None = None
    external_links: list[ExternalLink] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _crawl_requires_public_operator(self) -> Source:
        if self.policy is CrawlPolicy.crawl:
            if self.operator_kind not in CRAWLABLE_OPERATORS:
                raise ValueError(
                    f"{self.id}: policy=crawl は自治体または自治体の移住推進組織が運営主体の"
                    f" Source にしか設定できない（operator_kind={self.operator_kind}）"
                )
            if self.operator_evidence is None:
                raise ValueError(f"{self.id}: policy=crawl には operator_evidence（根拠）が必要")
            if not self.pages:
                raise ValueError(f"{self.id}: policy=crawl には pages が 1 つ以上必要")
        return self

    @property
    def crawlable(self) -> bool:
        return self.policy is CrawlPolicy.crawl
