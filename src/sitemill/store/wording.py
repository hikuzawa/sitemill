"""値が同じなら、LLM の言い回しを前回のまま残す（記録の無駄な書き換えを止める）。

同じ本文を読み直しても、LLM は言い直す。引用の頭の「*」が消える、住所の引用に施設名が
足される、見出しの語順が変わる。値（決定的パーサが決めた数・日付・時刻）は同じなのに
引用だけが動くので、次の 2 つが起きる。

- サイトマップの `lastmod` が「中身が変わった日」でなくなる（ADR 0025 の指紋が動く）
- 記録の履歴が、事実の変化ではなく言い直しで埋まる

そこで、**値が同じなら引用・見出しは前回のまま残す**。取得日時のような、毎回動くのが
正しい項目は新しい方を採る（鮮度の表示に使う）。

akiya-atlas は補助制度で同じ対処を入れている（2026-09-18。一晩で区分 4 件・要約 51 件が
言い直された）。japan-open-today は 2026-09-19 に実データで 1 晩を突き合わせ、値が同じまま
引用だけ動いたものが 3 施設あった。
"""

from __future__ import annotations

from typing import Any

# 言い回し。値が同じなら前回のまま残す
WORDING_KEYS = frozenset({"quote", "label", "reason", "summary", "title"})
# 毎回動くのが正しい項目。値が同じかどうかの判定からも外す
FRESHNESS_KEYS = frozenset({"fetched_at", "extracted_at", "checked_on", "checked_at"})


def _facts(node: Any) -> Any:
    """言い回しと取得日時を外した骨格。これが同じなら「値は同じ」とみなす。"""
    if isinstance(node, dict):
        return {
            k: _facts(v)
            for k, v in node.items()
            if k not in WORDING_KEYS and k not in FRESHNESS_KEYS
        }
    if isinstance(node, list):
        return [_facts(v) for v in node]
    return node


def same_facts(old: Any, new: Any) -> bool:
    """言い回しと取得日時を除いて同じか。"""
    return _facts(old) == _facts(new)


def freeze_wording(old: Any, new: Any) -> Any:
    """`new` を返す。ただし値が同じなら、言い回しは `old` のものを残す。

    値が 1 つでも違えば `new` をそのまま返す（言い回しも新しい本文に合わせる）。
    並びは長さが同じときだけ突き合わせる（要素が増減したら新しい方を採る）。
    """
    if not same_facts(old, new):
        return new
    return _merge(old, new)


def _merge(old: Any, new: Any) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        out: dict[str, Any] = {}
        for key, value in new.items():
            if key in WORDING_KEYS and key in old:
                out[key] = old[key]
            elif key in old:
                out[key] = _merge(old[key], value)
            else:
                out[key] = value
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [_merge(a, b) for a, b in zip(old, new, strict=True)]
    return new
