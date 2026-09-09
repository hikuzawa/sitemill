"""和暦・西暦の年と日付のパーサ。「築40年」のような相対表現は推定せず unparsed にする。"""

from __future__ import annotations

import re
from datetime import date

from sitemill.parse.jp.numbers import normalize_text

ERA_BASE = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
_ERA_ABBR = {"M": "明治", "T": "大正", "S": "昭和", "H": "平成", "R": "令和"}
_ERA_YEAR = re.compile(r"(?P<era>明治|大正|昭和|平成|令和)\s*(?P<y>元|\d{1,2})\s*年?")
_ERA_ABBR_YEAR = re.compile(r"(?<![A-Za-z0-9])(?P<era>[MTSHR])\s?(?P<y>\d{1,2})(?=\s*(?:年|\.|/))")
_WESTERN_YEAR = re.compile(r"(?<![\d.])(?P<y>1[89]\d{2}|20\d{2})(?![\d])\s*年?")
_RELATIVE = re.compile(r"築\s*(?:約|およそ)?\s*\d+\s*年")
_UNKNOWN = re.compile(r"不詳|不明|未詳")
_APPROX = re.compile(r"頃|ころ|約|およそ")
_ERA_DATE = re.compile(
    r"(?P<era>明治|大正|昭和|平成|令和)\s*(?P<y>元|\d{1,2})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
)
_W_DATE = re.compile(r"(?P<y>\d{4})\s*[年/.\-]\s*(?P<m>\d{1,2})\s*[月/.\-]\s*(?P<d>\d{1,2})\s*日?")


def _era_to_year(era: str, y: str) -> int:
    n = 1 if y == "元" else int(y)
    return ERA_BASE[era] + n


def _sane(year: int) -> bool:
    return 1850 <= year <= 2100


def parse_year(text: str) -> tuple[int | None, str | None]:
    """年（西暦）を返す。返り値は (値, 失敗理由)。"""
    t = normalize_text(text)
    if _UNKNOWN.search(t):
        return None, "unknown"
    m = _ERA_YEAR.search(t)
    if m:
        year = _era_to_year(m.group("era"), m.group("y"))
        return (year, None) if _sane(year) else (None, "out_of_range")
    m = _ERA_ABBR_YEAR.search(t)
    if m:
        year = _era_to_year(_ERA_ABBR[m.group("era")], m.group("y"))
        return (year, None) if _sane(year) else (None, "out_of_range")
    m = _WESTERN_YEAR.search(t)
    if m:
        year = int(m.group("y"))
        note = "approx" if _APPROX.search(t) else None
        return (year, note) if _sane(year) else (None, "out_of_range")
    if _RELATIVE.search(t):
        return None, "relative_age"
    return None, "no_year"


def parse_date(text: str) -> tuple[date | None, str | None]:
    """日付を返す。返り値は (値, 失敗理由)。"""
    t = normalize_text(text)
    m = _ERA_DATE.search(t)
    if m:
        year = _era_to_year(m.group("era"), m.group("y"))
        return _build(year, int(m.group("m")), int(m.group("d")))
    m = _W_DATE.search(t)
    if m:
        return _build(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    return None, "no_date"


def _build(y: int, m: int, d: int) -> tuple[date | None, str | None]:
    try:
        return date(y, m, d), None
    except ValueError:
        return None, "invalid_date"
