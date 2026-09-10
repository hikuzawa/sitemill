"""所在地の正規化。番地・号・建物名を落とし「市町村＋大字・地区名」までにする。

方針: 個人の住居を特定できる細かさ（番地・号・部屋番号）は保持しない。丁目は地区の単位なので残す。
漢数字は地名に多く含まれる（六日町、三本柳など）ため、番地・号・丁目が続く場合だけ数字とみなす。
"""

from __future__ import annotations

import re
import unicodedata

_KANJI = "〇一二三四五六七八九十百千"
# 「3丁目」「一丁目」は地区として残す
_CHOME = re.compile(rf"(?:\d+|[{_KANJI}]+)丁目")
# ここから先は番地・号・建物名: 算用数字、または 番地/番/号 が続く漢数字。
# 直前の 甲乙丙丁戊 や「字」も一緒に落とす
_STREET = re.compile(rf"[\s　]*(?:字\s*)?(?:[甲乙丙丁戊]\s*)?(?:\d|[{_KANJI}]+(?:番地|番|号))")
_TRAIL = re.compile(r"[\s　\-－‐‑–—ー・,、。／/]+$")


def has_street_number(text: str) -> bool:
    """番地らしき部分（算用数字、または 番地/番/号 付きの漢数字）が含まれるか。"""
    t = unicodedata.normalize("NFKC", text)
    t = _CHOME.sub("丁目", t)  # 丁目の数字は対象外
    return bool(_STREET.search(t))


def strip_street_number(address: str) -> tuple[str, str | None]:
    """(残した部分, 落とした部分) を返す。落とすものが無ければ (正規化した元の文字列, None)。"""
    t = unicodedata.normalize("NFKC", address).strip()
    if not t:
        return "", None
    cut_from = len(t)
    search_from = 0
    chome = _CHOME.search(t)
    if chome:
        search_from = chome.end()
    m = _STREET.search(t, search_from)
    if m:
        cut_from = m.start()
    kept = _TRAIL.sub("", t[:cut_from]).strip()
    removed = t[cut_from:].strip() or None
    if not kept:  # 数字だけの入力などは何も残せない
        return "", t
    return kept, removed
