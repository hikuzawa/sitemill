"""取得した生 HTML のローカルキャッシュ。git 管理外（ADR 0002）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sitemill.diff.hasher import url_key
from sitemill.store.jsonio import read_json, write_json


class RawCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, source_id: str, url: str) -> Path:
        return self.root / source_id / f"{url_key(url)}.html"

    def save(self, source_id: str, url: str, content: bytes, meta: dict[str, Any]) -> Path:
        path = self.path_for(source_id, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        write_json(path.with_suffix(".json"), {"url": url, **meta})
        return path

    def matches_state(self, source_id: str, url: str, content_hash: str | None) -> bool:
        """このキャッシュが、巡回状態の指す本文と同じものか。

        比べるのは保存時にメタへ記録した `content_hash`（正規化後のハッシュで、状態と同じ計算）。
        本文から計算し直さないのは、サービスが `ignore_patterns` などを変えただけで全ページが
        食い違いになり、取り直しと再抽出が一斉に起きるため。

        キャッシュは状態より古くなりうる。CI のキャッシュは成功した実行でしか保存されないが、
        状態のコミットは配置より前にある。「状態はコミットしたが配置で落ちた」実行の次は、
        古いキャッシュと新しい状態（ETag）の組になり、条件付き GET が 304 を返して古い本文を
        読むことになる（ADR 0024 追記）。
        """
        if content_hash is None or not self.path_for(source_id, url).is_file():
            return False
        meta = read_json(self.path_for(source_id, url).with_suffix(".json"), {}) or {}
        return meta.get("content_hash") == content_hash

    def load(self, source_id: str, url: str) -> tuple[bytes, dict[str, Any]] | None:
        path = self.path_for(source_id, url)
        if not path.is_file():
            return None
        return path.read_bytes(), read_json(path.with_suffix(".json"), {}) or {}

    def load_text(self, source_id: str, url: str) -> str | None:
        loaded = self.load(source_id, url)
        if loaded is None:
            return None
        content, meta = loaded
        enc = meta.get("encoding")
        if enc and enc != "binary":
            try:
                return content.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
        from sitemill.fetch.decode import decode_html  # 循環インポート回避

        return decode_html(content, meta.get("content_type"))[0]
