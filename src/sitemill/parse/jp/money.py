"""金額パーサ。「1,200万円」「1億2,000万円」「6.8万円」「68,000円」を円の整数にする。"""

from __future__ import annotations

import re
from decimal import Decimal

from sitemill.parse.jp.numbers import RANGE, has_digit, normalize_text, to_decimal

_TOKEN = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>億|万|千)?(?P<yen>\s*円)?")
# 数字の直後にこれらが続くなら金額ではない（面積・年・間取りなど）
_NOT_MONEY_AFTER = re.compile(
    r"\s*(m2|㎡|平米|坪|年|月|日|%|LDK|DK|SLDK|SDK|K|階|台|畳|帖|人|件|号|丁目|番)"
)
_UNITS = {"億": Decimal(100_000_000), "万": Decimal(10_000), "千": Decimal(1_000)}
_NEGOTIABLE = re.compile(r"応相談|要相談|相談|お問い?合わせ|問合せ|未定")


def parse_yen(text: str) -> tuple[int | None, str | None]:
    """金額を円の整数で返す。返り値は (値, 失敗理由)。"""
    t = normalize_text(text)
    if not has_digit(t):
        return None, "negotiable" if _NEGOTIABLE.search(t) else "no_number"
    if RANGE.search(t):
        return None, "range"

    amounts: list[Decimal] = []
    current: Decimal | None = None
    current_is_money = False
    last_end = -1
    for m in _TOKEN.finditer(t):
        unit, yen = m.group("unit"), m.group("yen")
        if not unit and not yen and _NOT_MONEY_AFTER.match(t, m.end()):
            continue
        num = to_decimal(m.group("num"))
        if num is None:
            continue
        val = num * _UNITS.get(unit or "", Decimal(1))
        adjacent = last_end >= 0 and t[last_end : m.start()].strip() == ""
        if adjacent and current is not None:
            current += val
            current_is_money = current_is_money or bool(unit or yen)
        else:
            if current is not None and current_is_money:
                amounts.append(current)
            current, current_is_money = val, bool(unit or yen)
        last_end = m.end()
        if yen:
            amounts.append(current)
            current, current_is_money, last_end = None, False, -1
    if current is not None and current_is_money:
        amounts.append(current)

    distinct = sorted(set(amounts))
    if not distinct:
        return None, "no_number"
    if len(distinct) > 1:
        return None, "multiple"
    value = distinct[0]
    if value != value.to_integral_value():
        return None, "fraction"
    return int(value), None
