"""URL ごとの巡回状態。JSON にコミットして環境をまたいで引き継ぐ（ADR 0002, 0003）。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from sitemill.store.jsonio import write_json


class UrlState(BaseModel):
    url: str
    source_id: str
    kind: str = "other"
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: datetime | None = None
    changed_at: datetime | None = None
    status: int | None = None
    error: str | None = None
    error_count: int = 0
    seen_count: int = 0
    pending_extract: bool = False
    extracted_hash: str | None = None
    extracted_at: datetime | None = None
    prompt_version: str | None = None


class CrawlState(BaseModel):
    version: int = 1
    urls: dict[str, UrlState] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> CrawlState:
        if path.is_file():
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        return cls()

    def save(self, path: Path) -> None:
        write_json(path, self.model_dump(mode="json"))

    def get(self, url: str) -> UrlState | None:
        return self.urls.get(url)

    def get_or_create(self, url: str, source_id: str, kind: str) -> UrlState:
        st = self.urls.get(url)
        if st is None:
            st = UrlState(url=url, source_id=source_id, kind=kind)
            self.urls[url] = st
        else:
            st.kind = kind
            st.source_id = source_id
        return st

    def for_source(self, source_id: str) -> list[UrlState]:
        return sorted(
            (s for s in self.urls.values() if s.source_id == source_id), key=lambda s: s.url
        )

    def forget(self, urls: Iterable[str]) -> int:
        """情報源から外した URL の状態を捨てる。残すと、seed に無いページを取り続ける。"""
        gone = [u for u in urls if u in self.urls]
        for url in gone:
            del self.urls[url]
        return len(gone)

    def pending(self, source_id: str | None = None) -> list[UrlState]:
        return [
            s
            for s in sorted(self.urls.values(), key=lambda s: s.url)
            if s.pending_extract
            and s.error is None
            and (source_id is None or s.source_id == source_id)
        ]
