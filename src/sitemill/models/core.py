"""共通モデル: 値と根拠を一緒に持つ FieldValue と、出所を表す Provenance。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from sitemill.models.license import LicenseVerdict


def utcnow() -> datetime:
    """秒精度の UTC 現在時刻。JSON に書いたときに読みやすくする。"""
    return datetime.now(UTC).replace(microsecond=0)


class FieldStatus(StrEnum):
    parsed = "parsed"
    not_found = "not_found"
    unparsed = "unparsed"
    quote_not_in_source = "quote_not_in_source"


class FieldValue[T](BaseModel):
    """LLM が返した引用（quote）と、決定的パーサが作った値（value）の組。

    value が入るのは status が parsed のときだけ。それ以外は note に理由を持つ。
    """

    value: T | None = None
    quote: str | None = None
    status: FieldStatus = FieldStatus.not_found
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is FieldStatus.parsed and self.value is not None


class ExtractorInfo(BaseModel):
    provider: str
    model: str
    prompt_version: str
    extracted_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached: bool = False


class Provenance(BaseModel):
    """一次情報の URL・取得日時・本文ハッシュ。全レコードが必ず持つ。"""

    source_url: str
    fetched_at: datetime
    content_hash: str
    page_kind: str | None = None
    extractor: ExtractorInfo | None = None
    license: LicenseVerdict | None = None
