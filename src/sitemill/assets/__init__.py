"""ライセンス通過画像の取得・保存（ADR 0020。設計は ADR 0005・0008）。

既定は不採用。`LicenseVerdict.allowed` が真の証拠がある画像だけを取得し、元 URL・ライセンス・
取得日時・クレジット文を隣に JSON で残す。ビルド時に「登録されていない `<img>`」があれば止める。

保存先は `data/assets/<source_id>/<sha256 の先頭 16>.<ext>`。生 HTML と同じくローカルキャッシュ
扱いで git 管理外にする（サービスの .gitignore）。provenance だけはコミットしてよい。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.models import LicenseVerdict
from sitemill.store.jsonio import read_json, write_json

log = logging.getLogger(__name__)

EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
}


class AssetError(RuntimeError):
    """取得しない・保存しない理由。既定が不採用なので、通らない理由を必ず言葉で残す。"""


@dataclass(frozen=True)
class AssetPolicy:
    allow_kinds: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp", "image/svg+xml")
    # 取得しない URL の部分文字列（所有者提供の写真など、サービスの方針で除く）
    exclude_patterns: tuple[str, ...] = ()
    max_bytes: int = 5_000_000
    min_bytes: int = 1_000  # 1KB 未満は透明画像・アイコンの類

    def content_type_ok(self, content_type: str) -> bool:
        return content_type.split(";")[0].strip().lower() in self.allow_kinds

    def excluded(self, url: str) -> str | None:
        for pattern in self.exclude_patterns:
            if pattern in url:
                return pattern
        return None


@dataclass
class Asset:
    """採用した画像 1 件。`asset_id` はページの `data-sitemill-asset` に入れる印。"""

    asset_id: str
    source_url: str
    local_path: Path
    content_type: str
    byte_size: int
    license: LicenseVerdict | None
    fetched_at: datetime
    credit_text: str
    author: str | None = None  # 作者名。多言語のクレジットを組み立てるのに使う
    alt_text: str = ""
    page_url: str | None = None  # 画像の説明ページ（Commons のファイルページなど）
    own_work: bool = False  # 自作の図版。第三者の著作物ではない
    tags: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """ページに出してよいか。自作か、ライセンス判定を通ったものだけ。"""
        return self.own_work or bool(self.license and self.license.allowed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "source_url": self.source_url,
            "local_path": self.local_path.name,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "license": self.license.model_dump(mode="json") if self.license else None,
            "fetched_at": self.fetched_at.isoformat(),
            "credit_text": self.credit_text,
            "author": self.author,
            "alt_text": self.alt_text,
            "page_url": self.page_url,
            "own_work": self.own_work,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, directory: Path) -> Asset:
        license_data = data.get("license")
        return cls(
            asset_id=data["asset_id"],
            source_url=data["source_url"],
            local_path=directory / data["local_path"],
            content_type=data["content_type"],
            byte_size=int(data.get("byte_size", 0)),
            license=LicenseVerdict.model_validate(license_data) if license_data else None,
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
            credit_text=data.get("credit_text", ""),
            author=data.get("author"),
            alt_text=data.get("alt_text", ""),
            page_url=data.get("page_url"),
            own_work=bool(data.get("own_work", False)),
            tags=list(data.get("tags", [])),
        )


def asset_id_for(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


class AssetStore:
    """1 source 分の画像と provenance を持つ。"""

    def __init__(self, root: Path, source_id: str) -> None:
        self.directory = root / source_id
        self.source_id = source_id

    @property
    def index_path(self) -> Path:
        return self.directory / "assets.json"

    def load(self) -> dict[str, Asset]:
        data = read_json(self.index_path) or {}
        rows = data.get("assets", []) if isinstance(data, dict) else []
        out: dict[str, Asset] = {}
        for row in rows:
            try:
                asset = Asset.from_dict(row, directory=self.directory)
            except (KeyError, ValueError) as e:
                log.warning("%s: 壊れた資産の記録を読み飛ばす: %s", self.source_id, e)
                continue
            out[asset.asset_id] = asset
        return out

    def save(self, assets: Iterable[Asset]) -> Path:
        rows = [a.to_dict() for a in sorted(assets, key=lambda a: a.asset_id)]
        write_json(self.index_path, {"source_id": self.source_id, "assets": rows})
        return self.index_path

    def add(self, asset: Asset) -> dict[str, Asset]:
        assets = self.load()
        assets[asset.asset_id] = asset
        self.save(assets.values())
        return assets


def fetch_asset(
    url: str,
    verdict: LicenseVerdict,
    policy: AssetPolicy,
    store: AssetStore,
    *,
    client: Any,
    credit_text: str | None = None,
    author: str | None = None,
    alt_text: str = "",
    page_url: str | None = None,
    tags: Iterable[str] = (),
) -> Asset:
    """ライセンス判定を通った画像を取得して保存する。通らないものは AssetError で拒む。

    `client` は `PoliteClient`（robots と間隔を守る）。ここでは判定を**しない**。
    判定は `license.detector` か `assets.commons` が済ませ、その結果だけを受け取る。
    """
    if not verdict.allowed:
        raise AssetError(f"ライセンス未通過の画像は取得しない: {url}（{verdict.reason}）")
    excluded = policy.excluded(url)
    if excluded is not None:
        raise AssetError(f"サービスの方針で除外: {url}（一致: {excluded}）")

    res = client.get(url)
    if not res.ok:
        raise AssetError(f"取得できない: {url}（status={res.status} {res.error or ''}）")
    content_type = (res.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not policy.content_type_ok(content_type):
        raise AssetError(f"扱わない種類: {url}（content-type={content_type or '不明'}）")
    size = len(res.content)
    if size > policy.max_bytes:
        raise AssetError(f"大きすぎる: {url}（{size} バイト > {policy.max_bytes}）")
    if size < policy.min_bytes:
        raise AssetError(f"小さすぎる: {url}（{size} バイト < {policy.min_bytes}）")

    asset_id = asset_id_for(res.content)
    store.directory.mkdir(parents=True, exist_ok=True)
    path = store.directory / f"{asset_id}{EXTENSIONS.get(content_type, '.bin')}"
    path.write_bytes(res.content)
    asset = Asset(
        asset_id=asset_id,
        source_url=url,
        local_path=path,
        content_type=content_type,
        byte_size=size,
        license=verdict,
        fetched_at=res.fetched_at,
        credit_text=credit_text or verdict.credit_text or "",
        author=author,
        alt_text=alt_text,
        page_url=page_url,
        tags=list(tags),
    )
    store.add(asset)
    log.info("%s: 画像を採用 %s（%s）", store.source_id, asset_id, verdict.label)
    return asset
