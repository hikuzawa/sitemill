"""所在地の正規化。番地・号・建物名を落とし「市町村＋大字・地区名」までにする。

方針: 個人の住居を特定できる細かさ（番地・号・部屋番号）は保持しない。丁目は地区の単位なので残す。
北海道に多い「北1条西2丁目」の条も同じく地区の単位として残す。
漢数字は地名に多く含まれる（六日町、三本柳など）ため、番地・号・丁目が続く場合だけ数字とみなす。
"""

from __future__ import annotations

import re
import unicodedata

_KANJI = "〇一二三四五六七八九十百千"
# 「3丁目」「一丁目」「北1条」は地区の単位として残す（北海道の条丁目を含む）
_DISTRICT = re.compile(rf"(?:\d+|[{_KANJI}]+)(?:丁目|条)")
# ここから先は番地・号・建物名: 算用数字、または 番地/号 が続く漢数字。
# 「二番町」等の地名を誤除去しないため、漢数字＋単独の「番」は番地とみなさない。
# 直前の 甲乙丙丁戊 や「字」も一緒に落とす
_STREET = re.compile(rf"[\s　]*(?:字\s*)?(?:[甲乙丙丁戊]\s*)?(?:\d|[{_KANJI}]+(?:番地|号))")
_TRAIL = re.compile(r"[\s　\-‐‑–—ー・,、。／/]+$")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "−": "-"})


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def _mask_districts(text: str) -> str:
    """地区の単位（丁目・条）を同じ長さの記号に置き換える。

    長さを変えないので、この文字列で見つけた位置を元の文字列にそのまま使える。
    判定（has_street_number）と切り出し（strip_street_number）が食い違わないための土台。
    """
    return _DISTRICT.sub(lambda m: "〓" * len(m.group(0)), text)


def has_street_number(text: str) -> bool:
    """番地らしき部分（算用数字、または 番地/号 付きの漢数字）が含まれるか。"""
    return bool(_STREET.search(_mask_districts(_norm(text))))


def strip_street_number(address: str) -> tuple[str, str | None]:
    """(残した部分, 落とした部分) を返す。落とすものが無ければ (正規化した元の文字列, None)。"""
    t = _norm(address).strip()
    if not t:
        return "", None
    m = _STREET.search(_mask_districts(t))
    cut_from = m.start() if m else len(t)
    kept = _TRAIL.sub("", t[:cut_from]).strip()
    removed = t[cut_from:].strip() or None
    if not kept:  # 数字だけの入力などは何も残せない
        return "", t
    return kept, removed
