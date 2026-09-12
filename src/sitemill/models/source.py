"""巡回対象（Source）の定義。サービス側の YAML から読み込む。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, model_validator

from sitemill.models.license import LicenseVerdict


class CrawlPolicy(StrEnum):
    crawl = "crawl"
    link_only = "link_only"


class OperatorKind(StrEnum):
    """運営主体の種別。巡回してよいかはここと根拠（operator_evidence）で決まる（ADR 0017）。"""

    municipality = "municipality"  # 市区町村
    municipality_affiliated = "municipality_affiliated"  # 市区町村の関連組織・指定管理者
    prefecture = "prefecture"  # 都道府県
    tourism_association = "tourism_association"  # 観光協会・観光連盟
    facility_official = "facility_official"  # 施設自身の公式サイト（社寺・公益財団など）
    transport_operator = "transport_operator"  # 鉄道・バス・旅客船の事業者
    third_party = "third_party"  # 民間のまとめサイト・予約サイト
    unknown = "unknown"


# 公式と根拠づけできる種別。これ以外（third_party / unknown）は policy=crawl にできない。
OFFICIAL_OPERATORS = frozenset(
    {
        OperatorKind.municipality,
        OperatorKind.municipality_affiliated,
        OperatorKind.prefecture,
        OperatorKind.tourism_association,
        OperatorKind.facility_official,
        OperatorKind.transport_operator,
    }
)

# サービスが `crawlable_operator_kinds` を宣言しないときの既定（自治体だけ）。
# akiya-atlas はこの既定のまま動く（ADR 0017）。
DEFAULT_CRAWLABLE_OPERATORS = frozenset(
    {OperatorKind.municipality, OperatorKind.municipality_affiliated}
)
# 旧名。既定ゲートと同じ意味で残す
CRAWLABLE_OPERATORS = DEFAULT_CRAWLABLE_OPERATORS


class PageKind(StrEnum):
    """よく使うページ種別。`kind` は文字列なので、サービスは独自の種別を足してよい（ADR 0017）。"""

    listing_index = "listing_index"
    listing_detail = "listing_detail"
    subsidy = "subsidy"
    info = "info"
    notice = "notice"  # お知らせ・運休告知。巡回間隔の例外に使う（ADR 0018）
    other = "other"


class PageKindValue(str):
    """ページ種別の値。中身は文字列だが、旧来の Enum と同じく `.value` でも読める。

    種別を `str` に緩めたとき（ADR 0017）、既に `kind.value` と書いていた
    利用者を壊さないための互換。
    新しく書くコードは文字列としてそのまま扱う。
    """

    __slots__ = ()

    @property
    def value(self) -> str:
        return str(self)


# 種別は小文字の英数字と下線だけ。書き間違いを静かに通さないための制約。
PageKindStr = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$"), AfterValidator(PageKindValue)]


class FollowRule(BaseModel):
    """seed ページから辿るリンクの規則。pattern は絶対 URL に対する正規表現。"""

    pattern: str
    kind: PageKindStr = PageKindValue("listing_detail")
    max_links: int | None = None


class SeedPage(BaseModel):
    url: str
    kind: PageKindStr = PageKindValue("info")
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
            if self.operator_kind not in OFFICIAL_OPERATORS:
                raise ValueError(
                    f"{self.id}: policy=crawl は運営主体が公式と根拠づけできる Source にしか"
                    f"設定できない（operator_kind={self.operator_kind}）。"
                    "どのサービスが巡回してよいかはサービス側の宣言でさらに絞る（ADR 0017）"
                )
            if self.operator_evidence is None:
                raise ValueError(f"{self.id}: policy=crawl には operator_evidence（根拠）が必要")
            if not self.pages:
                raise ValueError(f"{self.id}: policy=crawl には pages が 1 つ以上必要")
        return self

    @property
    def crawlable(self) -> bool:
        return self.policy is CrawlPolicy.crawl
