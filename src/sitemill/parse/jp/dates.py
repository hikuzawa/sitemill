"""和暦・西暦の年と日付のパーサ。「築40年」のような相対表現は推定せず unparsed にする。

期間（「9月15日〜9月20日」）は `parse_date_range`。年が書かれていなければ呼び出し側が渡した年を
使い、終わりが始まりより前なら年をまたぐ期間として扱う（12月29日〜1月3日）。
"""

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


_RANGE_DASH = r"(?:〜|~|-|‐|–|—|ー|から|to|より)"
# 年は西暦（2025年・2025/）か和暦（令和7年・R7年・R7.）。**和暦の年を読まないと今年として扱い、
# 去年の告知を今年の休業にしてしまう**（「R7年11月11日(火)〜」を 2026 年と読んでいた。2026-09-17）
_MD = (
    r"(?:(?P<{p}y>\d{{4}})\s*[年/.\-]\s*"
    r"|(?P<{p}era>明治|大正|昭和|平成|令和|(?<![A-Za-z])[MTSHR])\s*(?P<{p}eray>元|\d{{1,2}})\s*[年.]\s*)?"
    r"(?P<{p}m>\d{{1,2}})\s*[月/.\-]\s*(?P<{p}d>\d{{1,2}})\s*日?"
)
# 曜日の添え書き「(水)」「（水曜日）」。年が書かれていないときの年の決め手にする
_WD = r"\s*(?:\((?P<{p}wd>[月火水木金土日])(?:曜日?)?[^)]*\)|\([^)]*\))?"
_MD_ONLY_DAY = r"(?P<ed2>\d{1,2})\s*日"
_DATE_RANGE = re.compile(
    _MD.format(p="s")
    + _WD.format(p="s")
    + rf"\s*{_RANGE_DASH}\s*"
    + r"(?:"
    + _MD.format(p="e")
    + r"|"
    + _MD_ONLY_DAY
    + r")"
    + _WD.format(p="e")
)
_SINGLE = re.compile(_MD.format(p="s") + _WD.format(p="s"))
_WEEKDAYS = "月火水木金土日"


def _build_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _written_year(g: dict[str, str | None], p: str) -> int | None:
    """原文に書かれた年（西暦か和暦）。書かれていなければ None。"""
    if g.get(f"{p}y"):
        return int(g[f"{p}y"] or 0)
    era = g.get(f"{p}era")
    if era:
        return _era_to_year(_ERA_ABBR.get(era, era), g.get(f"{p}eray") or "1")
    return None


def _pick_year(
    month: int, day: int, weekday: str | None, *, written: int | None, base: int
) -> tuple[int | None, str | None]:
    """年を決める。書かれていればそれ。書かれていなければ曜日が合う年（前年・今年・翌年）。

    3 つの年で同じ月日の曜日はすべて違うので、曜日が合う年は 1 つに決まる。
    曜日が書かれていなければ基準年。書かれた年と曜日が食い違えば値にしない。
    """
    if written is not None:
        if weekday:
            d = _build_date(written, month, day)
            if d is not None and _WEEKDAYS[d.weekday()] != weekday:
                return None, "weekday_mismatch"
        return written, None
    if not weekday:
        return base, None
    for candidate in (base, base - 1, base + 1):
        d = _build_date(candidate, month, day)
        if d is not None and _WEEKDAYS[d.weekday()] == weekday:
            return candidate, None
    return None, "weekday_mismatch"


def parse_date_range(text: str, *, year: int) -> tuple[tuple[date, date] | None, str | None]:
    """期間（開始日, 終了日）を返す。単独の日付はその 1 日だけの期間になる。

    `year` は年が書かれていないときに使う基準年。JST の「今年」を呼び出し側が渡す
    （実行環境が UTC だと年末年始に 1 年ずれるため、ここで暗黙に決めない）。
    ただし曜日が添えてあれば、曜日が合う年（前年・今年・翌年）を採る。「10月1日(水)」は
    2026 年なら木曜なので 2025 年の告知。書かれた年と曜日が食い違えば値にしない。
    """
    t = normalize_text(text)
    if not t:
        return None, "no_text"
    m = _DATE_RANGE.search(t)
    if m is not None:
        g = m.groupdict()
        sm, sd = int(g["sm"]), int(g["sd"])
        sy, why = _pick_year(sm, sd, g.get("swd"), written=_written_year(g, "s"), base=year)
        if sy is None:
            return None, why
        start = _build_date(sy, sm, sd)
        if g.get("em"):
            em, ed = int(g["em"]), int(g["ed"])
        else:
            # 「9月15日〜20日」。月は開始と同じ
            em, ed = sm, int(g["ed2"])
        ey = _written_year(g, "e") or sy
        end = _build_date(ey, em, ed)
        if start is None or end is None:
            return None, "invalid_date"
        if end < start and _written_year(g, "e") is None:
            # 年をまたぐ（12月29日〜1月3日）。終わりを翌年にする
            end = _build_date(end.year + 1, end.month, end.day)
            if end is None:
                return None, "invalid_date"
        ewd = g.get("ewd")
        if ewd and _WEEKDAYS[end.weekday()] != ewd:
            return None, "weekday_mismatch"
        return (start, end), None
    m = _SINGLE.search(t)
    if m is not None:
        g = m.groupdict()
        sm, sd = int(g["sm"]), int(g["sd"])
        sy, why = _pick_year(sm, sd, g.get("swd"), written=_written_year(g, "s"), base=year)
        if sy is None:
            return None, why
        one = _build_date(sy, sm, sd)
        if one is None:
            return None, "invalid_date"
        return (one, one), "single_day"
    return None, "no_date_range"
