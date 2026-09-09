"""面積パーサ。㎡・平米はそのまま、坪は換算して平方メートルにする。"""

from __future__ import annotations

import re

from sitemill.parse.jp.numbers import RANGE, has_digit, normalize_text

TSUBO_TO_M2 = 3.305785
_M2 = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?:m2|㎡|平米|平方メートル|平方 ?m)")
_TSUBO = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*坪")


def parse_area_m2(text: str) -> tuple[float | None, str | None]:
    """面積を平方メートルで返す。返り値は (値, 失敗理由)。坪からの換算は note で分かる。"""
    t = normalize_text(text)
    if not has_digit(t):
        return None, "no_number"
    if RANGE.search(t):
        return None, "range"
    m2 = [float(x.replace(",", "")) for x in _M2.findall(t)]
    if len(m2) > 1:
        return None, "multiple"
    if m2:
        return round(m2[0], 2), None
    tsubo = [float(x.replace(",", "")) for x in _TSUBO.findall(t)]
    if len(tsubo) > 1:
        return None, "multiple"
    if tsubo:
        return round(tsubo[0] * TSUBO_TO_M2, 2), "from_tsubo"
    return None, "no_unit"
