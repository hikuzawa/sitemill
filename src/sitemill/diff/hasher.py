"""本文ハッシュと URL キー。"""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def url_key(url: str) -> str:
    """ファイル名に使う短い URL キー。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
