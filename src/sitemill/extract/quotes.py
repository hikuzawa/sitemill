"""引用の検証。LLM が返した引用が本文に実在するか、要約が本文の転載になっていないかを調べる。"""

from __future__ import annotations

import re

from sitemill.diff.normalize import squash

# 表のセルを繋ぐときに LLM が入れる区切り。本文には無い文字なので、そのままでは照合に落ちる。
# 「,」と「/」は桁区切りと日付（2,340円・03/21）で使われるので、数字に挟まれた位置では切らない。
# 切ってしまうと「2」「340円」のような短い断片になり、照合の意味が無くなる
_JOINERS = re.compile(r"[、；;・｜|]+|(?<!\d)\s*[,，/／]\s*(?!\d)")
# 断片ごとの照合を許す最小の長さ。短い断片は本文のどこかに偶然あるだけになる
MIN_FRAGMENT_CHARS = 4


def quote_in_source(quote: str, source_squashed: str) -> bool:
    """引用が本文中に逐語で存在するか。空白と全角半角の違いは無視する。"""
    q = squash(quote)
    return bool(q) and q in source_squashed


def verify_quote(quote: str, source_squashed: str) -> tuple[bool, str | None]:
    """引用が本文に実在するかを返す。返り値は (実在するか, 注記)。

    全体が逐語で見つかればそれが最良。見つからないとき、**区切りで切った断片すべてが
    逐語で見つかる**なら実在と認める（注記 `quote_joined`）。

    これを認める理由は、表から値を取るときに LLM が行を繋いで返すためである。寒霞渓の
    営業時間は 4 季節の表で、本文では各セルが改行で区切られている。LLM はこれを
    「03/21~10/20 8:30~17:00、10/21~11/30 8:00~17:00、…」と返す。「、」は本文に無いので
    全体照合では落ちるが、値はすべて本文のものである。料金表でも同じことが起きる。

    断片は 1 つでも本文に無ければ認めない。数値を作った引用はここで落ちる。
    """
    q = squash(quote)
    if not q:
        return False, "empty_quote"
    if q in source_squashed:
        return True, None
    fragments = [squash(f) for f in _JOINERS.split(quote)]
    fragments = [f for f in fragments if f]
    if len(fragments) < 2 or any(len(f) < MIN_FRAGMENT_CHARS for f in fragments):
        return False, None
    if all(f in source_squashed for f in fragments):
        return True, "quote_joined"
    return False, None


def verbatim_overlap(text: str, source_squashed: str, min_chars: int = 30) -> bool:
    """text の中に本文と min_chars 文字以上一致する連続部分があれば True（転載の疑い）。"""
    t = squash(text)
    if len(t) < min_chars or not source_squashed:
        return False
    return any(t[i : i + min_chars] in source_squashed for i in range(len(t) - min_chars + 1))
