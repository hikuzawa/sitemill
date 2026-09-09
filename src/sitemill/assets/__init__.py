"""ライセンス通過画像の取得・保存のインターフェース。今フェーズは設計のみ（ADR 0008）。

実装するときの契約:
- fetch_asset は必ず detect_license の LicenseVerdict.allowed が True の場合にだけ取得する
- 保存先は data/assets/<source_id>/<sha>.<ext>。隣に provenance
  （元 URL・ライセンス・取得日・クレジット）を JSON で置く
- 物件写真は所有者提供の可能性が高いため、サービス側の方針で除外できる
  （AssetPolicy.exclude_patterns）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sitemill.models import LicenseVerdict


@dataclass(frozen=True)
class AssetPolicy:
    allow_kinds: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp", "image/svg+xml")
    exclude_patterns: tuple[str, ...] = ()
    max_bytes: int = 5_000_000


@dataclass
class Asset:
    source_url: str
    local_path: Path
    content_type: str
    license: LicenseVerdict
    fetched_at: datetime
    credit_text: str
    alt_text: str = ""
    tags: list[str] = field(default_factory=list)


def fetch_asset(url: str, verdict: LicenseVerdict, policy: AssetPolicy, dest_dir: Path) -> Asset:
    """ライセンス通過画像を取得して保存する。未実装（インターフェースのみ）。"""
    if not verdict.allowed:
        raise PermissionError(f"ライセンス未通過の画像は取得しない: {url}（{verdict.reason}）")
    raise NotImplementedError("assets.fetch_asset は次フェーズで実装する（ADR 0008）")
