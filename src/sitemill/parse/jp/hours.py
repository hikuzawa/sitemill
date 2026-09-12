"""開館時間の決定的パーサ（ADR 0018）。LLM が返した原文の引用だけを受け取り、値にする。

読める形（実際の公式サイトに出る書き方）:
    10:00〜17:00
    9:00～17:00（入館は16:30まで）
    午前9時〜午後5時 / 9時〜17時30分
    平日 9:00〜17:00、土日祝 9:00〜18:00
    3月〜9月 9:00〜17:00 / 10月〜2月 9:00〜16:30
    10:00-17:00（最終入館 16:30）

読めない形は値にしない（None を返す）。月ごとに時間が変わる表などは、原文を引用として残したまま
「開館時間は不明」として扱い、一次情報へ送る。推測して埋めるより確実に不明と言うほうが安全。
"""

from __future__ import annotations

import re
from datetime import time

from sitemill.models.schedule import AnnualSpan, DaySelector, HoursPeriod, TimeRange
from sitemill.parse.jp.closures import parse_day_selector
from sitemill.parse.jp.numbers import normalize_text

# 区切り（NFKC 後は ～ が ~ に、〜 はそのまま残る）
DASH = r"(?:〜|~|-|‐|–|—|ー|から|to)"
_SEGMENT_SPLIT = re.compile(r"[、,;；/／\n]|(?<![0-9])・(?![0-9])")
_CLOCK = re.compile(
    r"(?P<ampm>午前|午後|am|pm)?\s*(?P<h>\d{1,2})\s*(?::|時)\s*(?P<m>\d{1,2})?\s*分?", re.I
)
_HOUR_ONLY = re.compile(r"(?P<ampm>午前|午後|am|pm)?\s*(?P<h>\d{1,2})\s*時(?![間])", re.I)
_LAST_ENTRY = re.compile(
    r"(?:最終|最後の)?(?:入館|入園|入場|入館受付|受付|入館時間|チケット販売)[^0-9]{0,6}"
    r"(?P<clock>\d{1,2}\s*(?::|時)\s*(?:\d{1,2})?\s*分?)"
)
_SEASON = re.compile(
    rf"(?P<sm>\d{{1,2}})\s*月\s*(?:(?P<sd>\d{{1,2}})\s*日)?\s*{DASH}\s*"
    rf"(?P<em>\d{{1,2}})\s*月\s*(?:(?P<ed>\d{{1,2}})\s*日)?"
)
_NO_TIME_MARKERS = re.compile(r"24\s*時間|終日|常時")


def parse_clock(text: str) -> time | None:
    """「10:00」「9時30分」「午後5時」を time にする。読めなければ None。"""
    t = normalize_text(text)
    for pattern in (_CLOCK, _HOUR_ONLY):
        m = pattern.search(t)
        if m is None:
            continue
        hour = int(m.group("h"))
        minute = int(m.groupdict().get("m") or 0)
        ampm = (m.groupdict().get("ampm") or "").lower()
        if ampm in ("午後", "pm") and hour < 12:
            hour += 12
        if ampm in ("午前", "am") and hour == 12:
            hour = 0
        # 「24:00」「25:00」は翌日 0 時台の表記。0〜1 時台に畳む
        if hour in (24, 25, 26):
            hour -= 24
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            return None
        return time(hour, minute)
    return None


_CLOCK_TOKEN = re.compile(
    r"(?:午前|午後|am|pm)?\s*\d{1,2}\s*(?::\s*\d{1,2}|時(?:\s*\d{1,2}\s*分?)?)", re.I
)


def _clock_tokens(text: str) -> list[str]:
    """時刻らしい部分文字列を出現順に返す。"""
    return [m.group(0) for m in _CLOCK_TOKEN.finditer(text)]


def parse_time_ranges(text: str) -> list[TimeRange]:
    """1 つの断片から時間帯を取り出す。「10:00〜17:00」「9:00〜12:00 13:00〜17:00」。"""
    t = normalize_text(text)
    last_entry = None
    m = _LAST_ENTRY.search(t)
    if m is not None:
        last_entry = parse_clock(m.group("clock"))
        t = t[: m.start()] + " " + t[m.end() :]  # 締切の時刻を開始・終了と混ぜない

    ranges: list[TimeRange] = []
    tokens = _clock_tokens(t)
    # 「A〜B」の対を順に取る。奇数個なら最後は捨てる（片側だけでは時間帯にならない）
    for i in range(0, len(tokens) - 1, 2):
        start, end = parse_clock(tokens[i]), parse_clock(tokens[i + 1])
        if start is None or end is None:
            continue
        ranges.append(TimeRange(start=start, end=end, last_entry=last_entry))
        last_entry = None  # 締切は最初の時間帯にだけ付ける
    return ranges


def _season(text: str) -> tuple[AnnualSpan | None, str]:
    """季節の前置き（「3月〜9月」）を取り出し、残りの文字列を返す。"""
    m = _SEASON.search(text)
    if m is None:
        return None, text
    sm, em = int(m.group("sm")), int(m.group("em"))
    sd = int(m.group("sd") or 1)
    ed = int(m.group("ed") or 0)
    if ed == 0:
        # 終わりの日が書かれていなければ月末まで（2 月は 28 日として扱わず、月末判定は contains 側で
        # 月日の比較になるため 31 でよい）
        ed = 31
    try:
        span = AnnualSpan(start_month=sm, start_day=sd, end_month=em, end_day=ed)
    except ValueError:
        return None, text
    return span, text[: m.start()] + " " + text[m.end() :]


def parse_opening_hours(quote: str) -> tuple[list[HoursPeriod] | None, str | None]:
    """開館時間の引用を HoursPeriod の並びにする。返り値は (値, 注記)。

    quote-then-parse の約束どおり、読めなければ (None, 理由) を返して引用だけを残す。
    """
    if not quote or not quote.strip():
        return None, "no_text"
    text = normalize_text(quote)
    if _NO_TIME_MARKERS.search(text):
        # 「24時間」「終日」は時間帯として表せるが、施設ごとに意味が違う（入場自由 / 無人）。
        # 値にせず注記だけ残す
        return None, "always_open_text"

    periods: list[HoursPeriod] = []
    for segment in _SEGMENT_SPLIT.split(text):
        segment = segment.strip()
        if not segment:
            continue
        season, rest = _season(segment)
        ranges = parse_time_ranges(rest)
        if not ranges:
            continue
        days = parse_day_selector(rest) or DaySelector()
        periods.append(HoursPeriod(ranges=ranges, days=days, season=season, label=segment or None))
    if not periods:
        return None, "no_time_range"
    return periods, None
