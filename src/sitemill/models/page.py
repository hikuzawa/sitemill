"""ビルド用のページモデルと必須の信頼シグナル（ADR 0007）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SourceLink(BaseModel):
    label: str
    url: str
    fetched_at: datetime | None = None


class OperatorInfo(BaseModel):
    name: str
    contact: str
    # 連絡先が URL のときに信頼ブロックで見せる文字列。無ければ URL をそのまま出す
    contact_label: str | None = None
    url: str | None = None


class TrustSignals(BaseModel):
    """全ページ必須。欠けるとビルドが失敗する。"""

    updated_at: datetime
    sources: list[SourceLink] = Field(default_factory=list)
    operator: OperatorInfo
    record_count: int | None = None
    generated_at: datetime | None = None


class PageMeta(BaseModel):
    title: str
    description: str = ""
    path: str  # dist 内の相対パス。例: nagano/20219-tomi/index.html
    noindex: bool = False
    changefreq: str = "weekly"
    priority: float = 0.5
    og_type: str = "website"  # OGP の og:type
    structured_data: list[dict[str, Any]] = Field(default_factory=list)  # JSON-LD ノード

    @property
    def url_path(self) -> str:
        p = "/" + self.path.replace("\\", "/").lstrip("/")
        return p[: -len("index.html")] if p.endswith("index.html") else p


class Page(BaseModel):
    meta: PageMeta
    template: str
    context: dict[str, Any] = Field(default_factory=dict)
    trust: TrustSignals


class Redirect(BaseModel):
    from_path: str
    to_url: str
    status: int = 302
