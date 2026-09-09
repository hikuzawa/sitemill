"""HTML バイト列の文字コード判定。BOM → meta charset → HTTP ヘッダ → 自動判定の順（ADR 0003）。"""

from __future__ import annotations

import codecs
import re

from charset_normalizer import from_bytes

_META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)
_HEADER = re.compile(r"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)
_ALIASES = {
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "windows-31j": "cp932",
    "ms932": "cp932",
    "euc-jp": "euc_jp",
    "eucjp": "euc_jp",
    "x-euc-jp": "euc_jp",
    "utf8": "utf-8",
}
# サーバー既定値として付くだけのことが多く、日本語ページでは信用しない
_WEAK = {"iso8859-1", "latin-1", "cp1252", "ascii"}


def canonical_encoding(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    n = _ALIASES.get(n, n)
    try:
        return codecs.lookup(n).name
    except LookupError:
        return None


def decode_html(content: bytes, content_type: str | None = None) -> tuple[str, str]:
    """(本文, 使った文字コード) を返す。判定できなければ utf-8 で置換デコードする。"""
    if content.startswith(codecs.BOM_UTF8):
        return content[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace"), "utf-8"
    candidates: list[str] = []
    m = _META.search(content[:8192])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    if content_type:
        h = _HEADER.search(content_type)
        if h:
            candidates.append(h.group(1))
    tried: set[str] = set()
    for raw in candidates:
        enc = canonical_encoding(raw)
        if not enc or enc in tried or enc in _WEAK:
            continue
        tried.add(enc)
        try:
            return content.decode(enc), enc
        except UnicodeDecodeError:
            continue
    best = from_bytes(content).best()
    if best is not None:
        return str(best), canonical_encoding(best.encoding) or best.encoding
    return content.decode("utf-8", errors="replace"), "utf-8"
