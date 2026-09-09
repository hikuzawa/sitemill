"""全角・記号の正規化と数値の取り出し。"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 「300〜500」「300~500」「300から500」のような範囲表現
RANGE = re.compile(
    r"\d[\d,.]*\s*(?:億|万|千)?\s*(?:円|m2|坪|年)?\s*(?:〜|～|~|-|‐|–|—|ー|から)\s*\d"
)


def normalize_text(s: str) -> str:
    """NFKC 正規化と空白の圧縮。全角数字は半角に、㎡ は m2 になる。"""
    s = unicodedata.normalize("NFKC", s)
    return _WS.sub(" ", s).strip()


def to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def find_numbers(s: str) -> list[Decimal]:
    out: list[Decimal] = []
    for m in _NUM.finditer(normalize_text(s)):
        d = to_decimal(m.group(0))
        if d is not None:
            out.append(d)
    return out


def parse_int(s: str) -> int | None:
    """最初の整数を返す。小数や数値なしは None。"""
    nums = find_numbers(s)
    if not nums:
        return None
    d = nums[0]
    return int(d) if d == d.to_integral_value() else None


def has_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in normalize_text(s))
