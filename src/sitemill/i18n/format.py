"""ロケールごとの書式（日付・曜日・時刻・数値・金額）。ADR 0016。

書式そのものは data として持ち、コードは組み立てるだけにする。

ここは純粋な関数だけ。時間帯の変換（UTC → JST）は呼び出し側で済ませてから渡す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from sitemill.i18n.locale import base_language

# 時刻は全ロケールで 24 時間表記にする。原文（日本の公式サイト）が 24 時間表記で、
# 12 時間表記への変換は「17:00 を 5:00 と書く」種類の取り違えを生むため、変換しない。
TIME_24H = "{H}:{M}"


@dataclass(frozen=True)
class LocaleFormat:
    """1 ロケール分の書式。`{y} {m} {d} {H} {M} {wd} {month} {mon}` を置換する。"""

    date: str
    date_short: str
    datetime: str
    money: str
    weekdays: tuple[str, str, str, str, str, str, str]  # 月曜はじまり
    months: tuple[str, ...] | None = None  # 月名。None なら数字を使う
    time: str = TIME_24H
    group: str = ","  # 桁区切り
    range_sep: str = "–"  # 時刻の範囲（en dash）


JA = LocaleFormat(
    date="{y}年{m}月{d}日",
    date_short="{m}月{d}日（{wd}）",
    datetime="{y}年{m}月{d}日 {H}:{M}",
    money="{amount}円",
    weekdays=("月", "火", "水", "木", "金", "土", "日"),
)

ZH_HANT = LocaleFormat(
    date="{y}年{m}月{d}日",
    date_short="{m}月{d}日（{wd}）",
    datetime="{y}年{m}月{d}日 {H}:{M}",
    money="{amount}日圓",
    weekdays=("週一", "週二", "週三", "週四", "週五", "週六", "週日"),
)

EN = LocaleFormat(
    date="{d} {month} {y}",
    date_short="{wd}, {d} {mon}",
    datetime="{d} {month} {y} {H}:{M}",
    money="¥{amount}",
    weekdays=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    months=(
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
)

FORMATS: dict[str, LocaleFormat] = {"ja": JA, "zh-Hant": ZH_HANT, "zh": ZH_HANT, "en": EN}
FALLBACK = EN


def register_format(code: str, fmt: LocaleFormat) -> None:
    """サービス側でロケールを足すときに使う。"""
    FORMATS[code] = fmt


def fmt_for(code: str | None) -> LocaleFormat:
    """完全一致 → 言語部分の一致 → 英語。"""
    if not code:
        return FALLBACK
    if code in FORMATS:
        return FORMATS[code]
    return FORMATS.get(base_language(code), FALLBACK)


def _parts(fmt: LocaleFormat, *, d: date | None = None, t: time | None = None) -> dict[str, str]:
    parts: dict[str, str] = {}
    if d is not None:
        month_name = fmt.months[d.month - 1] if fmt.months else str(d.month)
        parts.update(
            y=str(d.year),
            m=str(d.month),
            d=str(d.day),
            wd=fmt.weekdays[d.weekday()],
            month=month_name,
            mon=month_name[:3] if fmt.months else month_name,
        )
    if t is not None:
        parts.update(H=f"{t.hour:02d}", M=f"{t.minute:02d}")
    return parts


def format_date(value: date, code: str | None) -> str:
    fmt = fmt_for(code)
    return fmt.date.format(**_parts(fmt, d=value))


def format_date_short(value: date, code: str | None) -> str:
    """曜日を含む短い日付。「今日は開いているか」の帯に使う。"""
    fmt = fmt_for(code)
    return fmt.date_short.format(**_parts(fmt, d=value))


def format_weekday(value: date, code: str | None) -> str:
    fmt = fmt_for(code)
    return fmt.weekdays[value.weekday()]


def format_time(value: time, code: str | None) -> str:
    fmt = fmt_for(code)
    return fmt.time.format(**_parts(fmt, t=value))


def format_time_range(start: time, end: time | None, code: str | None) -> str:
    fmt = fmt_for(code)
    head = format_time(start, code)
    return head if end is None else f"{head}{fmt.range_sep}{format_time(end, code)}"


def format_datetime(value: datetime, code: str | None) -> str:
    """渡された datetime をそのまま整える（時間帯の変換は呼び出し側で済ませる）。"""
    fmt = fmt_for(code)
    return fmt.datetime.format(**_parts(fmt, d=value.date(), t=value.time()))


def format_number(value: int | float, code: str | None) -> str:
    fmt = fmt_for(code)
    text = f"{value:,}" if isinstance(value, int) else f"{value:,.1f}"
    return text if fmt.group == "," else text.replace(",", fmt.group)


def format_money(yen: int, code: str | None) -> str:
    """日本円の金額。単位の位置と表記はロケールごとに変える。"""
    fmt = fmt_for(code)
    return fmt.money.format(amount=format_number(int(yen), code))
