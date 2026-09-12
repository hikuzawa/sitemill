"""定休日と運行日の決定的パーサ（ADR 0018）。原文の引用から規則を作る。

読める形:
    毎週月曜日 / 月曜・火曜 / 月〜水
    月曜日（祝日の場合は翌日）/（祝日の場合は翌平日）/（祝日の場合は開館）
    第2・第4水曜日
    年末年始（12月29日〜1月3日）/ 12/29〜1/3
    2026年9月15日〜9月20日（特定の日付は SpecialNotice 側で扱う）
    年中無休 / 無休 → 規則なし（空の並び）
    不定休 → ClosureKind.irregular（計算できないことを明示する）

運行日:
    平日ダイヤ / 土休日ダイヤ / 土日祝 / 毎日運航

「祝日の場合は翌日」と「翌平日」を区別するのが要点。祝日が連続する年（2026 年の 9/21〜9/23）では
「翌日」がまた祝日になり、原文だけではどちらに動くか決まらない。その場合は判定側で unknown にする。
"""

from __future__ import annotations

import re
from datetime import date

from sitemill.models.schedule import (
    AnnualSpan,
    ClosureKind,
    ClosureRule,
    DaySelector,
    HolidayBehavior,
)
from sitemill.parse.jp.numbers import normalize_text

DASH = r"(?:〜|~|-|‐|–|—|ー|から|to)"
_WEEKDAY_CHARS = "月火水木金土日"
WEEKDAY_INDEX = {ch: i for i, ch in enumerate(_WEEKDAY_CHARS)}

_ALWAYS_OPEN = re.compile(r"年中無休|無休|休館日なし|定休日なし|休業日なし")
_IRREGULAR = re.compile(r"不定休|不定期休")
_SEGMENT_SPLIT = re.compile(r"[、,;；\n]|および|及び")

# 「毎週月曜日」「月曜・火曜」「月〜水曜」
_WEEKDAY_RUN = re.compile(rf"([{_WEEKDAY_CHARS}])\s*曜?\s*{DASH}\s*([{_WEEKDAY_CHARS}])\s*曜")
_WEEKDAY_ONE = re.compile(rf"([{_WEEKDAY_CHARS}])\s*曜")
# 「第2・第4水曜日」「第1,3月曜」
_NTH = re.compile(r"第\s*([0-9０-９][0-9０-９,、・\s第]*)\s*([" + _WEEKDAY_CHARS + r"])\s*曜")

_NTH_NUMBERS = re.compile(r"\d")
# 「12月29日〜1月3日」「12/29〜1/3」
_ANNUAL_MD = re.compile(
    rf"(?P<sm>\d{{1,2}})\s*[月/]\s*(?P<sd>\d{{1,2}})\s*日?\s*{DASH}\s*"
    rf"(?:(?P<em>\d{{1,2}})\s*[月/]\s*)?(?P<ed>\d{{1,2}})\s*日?"
)
_YEAR_END = re.compile(r"年末年始")
_YEAR_END_DEFAULT = AnnualSpan(start_month=12, start_day=29, end_month=1, end_day=3)
_SPECIFIC_DATE = re.compile(r"(?:(?P<y>\d{4})\s*年\s*)?(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日")

# 祝日のときの扱い
_HOLIDAY_NEXT_WEEKDAY = re.compile(r"祝日?[^)）]{0,8}?翌\s*平日|翌\s*平日")
_HOLIDAY_NEXT_DAY = re.compile(r"祝日?[^)）]{0,8}?翌日|翌日\s*(?:休|閉)")
_HOLIDAY_OPEN = re.compile(r"祝日?[^)）]{0,8}?(?:開館|開園|営業|開場)")
_HOLIDAY_MENTION = re.compile(r"祝日?")

# 運行日・当てはまる日の指定
_WEEKDAYS_ONLY = re.compile(r"平日")
_SAT_HOLIDAY = re.compile(r"土休日|土曜?・?休日|土日祝|土・日・祝|週末・?祝日")
_EXCLUDE_HOLIDAY = re.compile(r"祝日?を?除く|祝日?除く|祝日?を?のぞく")
_HOLIDAY_INCLUDED = re.compile(r"祝日?も?含む|祝日?を?含む|・?祝")
_EVERY_DAY = re.compile(r"毎日|全日")


def _holiday_behavior(text: str) -> HolidayBehavior:
    """祝日に重なったときの扱いを読む。注記が無ければ規則どおり（closed）。"""
    if _HOLIDAY_NEXT_WEEKDAY.search(text):
        return HolidayBehavior.next_weekday
    if _HOLIDAY_NEXT_DAY.search(text):
        return HolidayBehavior.next_day
    if _HOLIDAY_OPEN.search(text):
        return HolidayBehavior.open
    if _HOLIDAY_MENTION.search(text):
        # 祝日に触れてはいるが、どう動くか読み取れない。推測しない
        return HolidayBehavior.unspecified
    return HolidayBehavior.closed


def _weekdays(text: str) -> tuple[int, ...]:
    """曜日の並びを読む。「月〜水」は範囲として展開する。"""
    found: set[int] = set()
    rest = text
    for m in _WEEKDAY_RUN.finditer(text):
        a, b = WEEKDAY_INDEX[m.group(1)], WEEKDAY_INDEX[m.group(2)]
        span = range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)]
        found.update(span)
        rest = rest.replace(m.group(0), " ")
    for m in _WEEKDAY_ONE.finditer(rest):
        found.add(WEEKDAY_INDEX[m.group(1)])
    return tuple(sorted(found))


def parse_day_selector(text: str) -> DaySelector | None:
    """「平日」「土休日」「土日祝」「月〜金」などの当てはまる日の指定。無ければ None。"""
    t = normalize_text(text)
    if _EVERY_DAY.search(t):
        return DaySelector()
    if _SAT_HOLIDAY.search(t):
        return DaySelector(weekdays=(5, 6), include_holidays=True)
    if _WEEKDAYS_ONLY.search(t):
        return DaySelector(weekdays=(0, 1, 2, 3, 4), exclude_holidays=True)
    weekdays = _weekdays(t)
    if weekdays:
        include = bool(_HOLIDAY_INCLUDED.search(t)) and not _EXCLUDE_HOLIDAY.search(t)
        return DaySelector(
            weekdays=weekdays,
            include_holidays=include,
            exclude_holidays=bool(_EXCLUDE_HOLIDAY.search(t)),
        )
    return None


def parse_service_days(quote: str) -> tuple[DaySelector | None, str | None]:
    """運行日（ダイヤ）の引用を DaySelector にする。返り値は (値, 注記)。

    「土休日ダイヤ」は土曜・日曜と**祝日**に運行する。祝日を曜日だけで判定すると、
    2026 年 9 月 21 日（月・敬老の日）の運行を取り違える。
    """
    if not quote or not quote.strip():
        return None, "no_text"
    selector = parse_day_selector(quote)
    if selector is None:
        return None, "no_service_days"
    return selector, None


def _annual_spans(text: str) -> list[tuple[AnnualSpan, str]]:
    spans: list[tuple[AnnualSpan, str]] = []
    for m in _ANNUAL_MD.finditer(text):
        sm, sd = int(m.group("sm")), int(m.group("sd"))
        em = int(m.group("em")) if m.group("em") else sm
        ed = int(m.group("ed"))
        try:
            spans.append(
                (
                    AnnualSpan(start_month=sm, start_day=sd, end_month=em, end_day=ed),
                    m.group(0),
                )
            )
        except ValueError:
            continue
    if not spans and _YEAR_END.search(text):
        # 「年末年始」だけで日付が書かれていない場合。一般的な 12/29〜1/3 を当てるが、
        # 施設ごとに違うので注記を残す
        spans.append((_YEAR_END_DEFAULT, "年末年始"))
    return spans


def _nth_rules(text: str, behavior: HolidayBehavior) -> list[ClosureRule]:
    rules: list[ClosureRule] = []
    for m in _NTH.finditer(text):
        nths = tuple(sorted({int(d) for d in _NTH_NUMBERS.findall(m.group(1))}))
        weekday = WEEKDAY_INDEX[m.group(2)]
        if not nths:
            continue
        rules.append(
            ClosureRule(
                kind=ClosureKind.nth_weekday,
                weekdays=(weekday,),
                nths=nths,
                holiday_behavior=behavior,
                label=m.group(0),
            )
        )
    return rules


def parse_closures(quote: str) -> tuple[list[ClosureRule] | None, str | None]:
    """定休日の引用を規則の並びにする。返り値は (値, 注記)。

    - 「年中無休」→ ([], "always_open") 規則が無いことを**読み取れた**状態
    - 「不定休」→ ([irregular の規則], "irregular") 計算できないことを明示する
    - 読めなければ (None, 理由) で引用だけ残す
    """
    if not quote or not quote.strip():
        return None, "no_text"
    text = normalize_text(quote)
    if _IRREGULAR.search(text):
        return [ClosureRule(kind=ClosureKind.irregular, label=text[:40])], "irregular"
    if _ALWAYS_OPEN.search(text):
        return [], "always_open"

    rules: list[ClosureRule] = []
    for segment in _SEGMENT_SPLIT.split(text):
        segment = segment.strip()
        if not segment:
            continue
        behavior = _holiday_behavior(segment)
        nth = _nth_rules(segment, behavior)
        rules.extend(nth)
        spans = _annual_spans(segment)
        for span, label in spans:
            rules.append(ClosureRule(kind=ClosureKind.annual_span, annual=span, label=label))
        if nth:
            continue  # 第 n 曜日を読めたなら、同じ断片の曜日は二重に数えない
        # 期間の日付（12月29日）を曜日と誤読しないよう、期間として読めた部分は除く
        rest = segment
        for _, label in spans:
            rest = rest.replace(label, " ")
        weekdays = _weekdays(rest)
        if weekdays:
            rules.append(
                ClosureRule(
                    kind=ClosureKind.weekly,
                    weekdays=weekdays,
                    holiday_behavior=behavior,
                    label=rest.strip()[:40] or None,
                )
            )
    if not rules:
        return None, "no_closure_rule"
    return rules, None


def parse_specific_dates(quote: str, *, year: int) -> tuple[tuple[date, ...] | None, str | None]:
    """「9月15日・9月16日」のような特定日の並びを読む。年は呼び出し側が渡す（JST の今年）。"""
    if not quote or not quote.strip():
        return None, "no_text"
    text = normalize_text(quote)
    out: list[date] = []
    for m in _SPECIFIC_DATE.finditer(text):
        try:
            out.append(date(int(m.group("y") or year), int(m.group("m")), int(m.group("d"))))
        except ValueError:
            continue
    if not out:
        return None, "no_date"
    return tuple(sorted(set(out))), None
