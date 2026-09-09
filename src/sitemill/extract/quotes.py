"""引用の検証。LLM が返した引用が本文に実在するか、要約が本文の転載になっていないかを調べる。"""

from __future__ import annotations

from sitemill.diff.normalize import squash


def quote_in_source(quote: str, source_squashed: str) -> bool:
    """引用が本文中に逐語で存在するか。空白と全角半角の違いは無視する。"""
    q = squash(quote)
    return bool(q) and q in source_squashed


def verbatim_overlap(text: str, source_squashed: str, min_chars: int = 30) -> bool:
    """text の中に本文と min_chars 文字以上一致する連続部分があれば True（転載の疑い）。"""
    t = squash(text)
    if len(t) < min_chars or not source_squashed:
        return False
    return any(t[i : i + min_chars] in source_squashed for i in range(len(t) - min_chars + 1))
