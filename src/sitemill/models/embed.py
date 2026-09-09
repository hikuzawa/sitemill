"""地図・SNS などの埋め込み。転載ではなく埋め込み機能経由のみ（ADR 0008）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class EmbedKind(StrEnum):
    map = "map"
    streetview = "streetview"
    youtube = "youtube"
    instagram = "instagram"
    x = "x"


class Embed(BaseModel):
    kind: EmbedKind
    provider: str
    src_url: str | None = None
    link_url: str
    title: str
    attribution: str
    license_note: str
    fetched_at: datetime | None = None
    width: int = 600
    height: int = 400

    @property
    def embeddable(self) -> bool:
        return self.src_url is not None
