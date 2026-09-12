"""開館時間・定休日・運行日・期間・所要時間のパーサ（ADR 0018）。"""

from __future__ import annotations

from datetime import date, time

from sitemill.models.schedule import ClosureKind, HolidayBehavior
from sitemill.parse.jp.closures import (
    parse_closures,
    parse_day_selector,
    parse_service_days,
    parse_specific_dates,
)
from sitemill.parse.jp.dates import parse_date_range
from sitemill.parse.jp.duration import parse_minutes
from sitemill.parse.jp.hours import parse_clock, parse_opening_hours, parse_time_ranges

# --- 時刻と時間帯 -----------------------------------------------------------


def test_parse_clock_forms() -> None:
    assert parse_clock("10:00") == time(10, 0)
    assert parse_clock("9時30分") == time(9, 30)
    assert parse_clock("１７：００") == time(17, 0)  # 全角
    assert parse_clock("午後5時") == time(17, 0)
    assert parse_clock("午前9時") == time(9, 0)
    assert parse_clock("24:00") == time(0, 0)
    assert parse_clock("お問い合わせください") is None


def test_time_range_with_last_entry() -> None:
    ranges = parse_time_ranges("9:00～17:00（入館は16:30まで）")
    assert len(ranges) == 1
    assert (ranges[0].start, ranges[0].end) == (time(9, 0), time(17, 0))
    assert ranges[0].last_entry == time(16, 30)


def test_two_ranges_for_a_lunch_break() -> None:
    ranges = parse_time_ranges("9:00〜12:00 13:00〜17:00")
    assert [(r.start, r.end) for r in ranges] == [
        (time(9, 0), time(12, 0)),
        (time(13, 0), time(17, 0)),
    ]


def test_opening_hours_splits_by_day_selector() -> None:
    periods, note = parse_opening_hours("平日 9:00〜17:00、土日祝 9:00〜18:00")
    assert note is None and periods is not None and len(periods) == 2
    weekday, weekend = periods
    assert weekday.days.weekdays == (0, 1, 2, 3, 4) and weekday.days.exclude_holidays
    assert weekend.days.weekdays == (5, 6) and weekend.days.include_holidays
    assert weekend.ranges[0].end == time(18, 0)


def test_opening_hours_with_a_season() -> None:
    periods, note = parse_opening_hours("3月〜9月 9:00〜17:00 / 10月〜2月 9:00〜16:30")
    assert note is None and periods is not None and len(periods) == 2
    summer, winter = periods
    assert summer.season is not None and summer.season.contains(date(2026, 5, 1))
    assert not summer.season.contains(date(2026, 12, 1))
    # 10月〜2月 は年をまたぐ
    assert winter.season is not None and winter.season.crosses_year
    assert winter.season.contains(date(2026, 1, 15))
    assert winter.ranges[0].end == time(16, 30)


def test_unreadable_hours_stay_unparsed() -> None:
    """読めない形を推測で埋めない。引用だけ残して「不明」と出す。"""
    for text in ("", "季節により変動", "お問い合わせください"):
        value, note = parse_opening_hours(text)
        assert value is None and note is not None


def test_explicit_free_access_becomes_a_value() -> None:
    """「入園自由」は時間帯が無いが、開いていることは分かる。unknown にしない。"""
    for text in ("24時間", "入園自由", "常時開放", "見学自由"):
        periods, note = parse_opening_hours(text)
        assert note == "always_open", text
        assert periods is not None and periods[0].always_open, text
        assert periods[0].ranges == [], text


# --- 定休日 -----------------------------------------------------------------


def test_weekly_closure() -> None:
    rules, note = parse_closures("毎週月曜日")
    assert note is None and rules is not None and len(rules) == 1
    assert rules[0].kind is ClosureKind.weekly
    assert rules[0].weekdays == (0,)
    assert rules[0].holiday_behavior is HolidayBehavior.closed


def test_holiday_behavior_variants() -> None:
    """「翌日」と「翌平日」を区別する。祝日が連続する年で結果が変わる。"""
    cases = {
        "月曜日（祝日の場合は翌日）": HolidayBehavior.next_day,
        "月曜日（祝日の場合は翌平日）": HolidayBehavior.next_weekday,
        "月曜日（祝日の場合は開館）": HolidayBehavior.open,
        "月曜日": HolidayBehavior.closed,
        "月曜日（祝日に関する記載あり）": HolidayBehavior.unspecified,
    }
    for text, expected in cases.items():
        rules, _ = parse_closures(text)
        assert rules is not None and rules[0].holiday_behavior is expected, text


def test_weekday_range_and_list() -> None:
    rules, _ = parse_closures("月曜・火曜")
    assert rules is not None and rules[0].weekdays == (0, 1)
    rules, _ = parse_closures("月〜水曜")
    assert rules is not None and rules[0].weekdays == (0, 1, 2)


def test_nth_weekday_multiple() -> None:
    rules, _ = parse_closures("第2・第4水曜日")
    assert rules is not None and len(rules) == 1
    assert rules[0].kind is ClosureKind.nth_weekday
    assert rules[0].weekdays == (2,) and rules[0].nths == (2, 4)


def test_year_end_span_crossing_the_year() -> None:
    rules, _ = parse_closures("年末年始（12月29日〜1月3日）")
    span = next(r.annual for r in rules or [] if r.kind is ClosureKind.annual_span)
    assert span is not None and span.crosses_year
    assert span.contains(date(2026, 12, 31)) and span.contains(date(2027, 1, 2))
    assert not span.contains(date(2026, 12, 28)) and not span.contains(date(2027, 1, 4))


def test_year_end_without_dates_uses_the_common_span() -> None:
    rules, _ = parse_closures("年末年始")
    span = next(r.annual for r in rules or [] if r.kind is ClosureKind.annual_span)
    assert span is not None and span.contains(date(2026, 12, 30))


def test_weekly_and_year_end_together() -> None:
    rules, _ = parse_closures("毎週月曜日、年末年始（12/29〜1/3）")
    assert rules is not None
    kinds = {r.kind for r in rules}
    assert kinds == {ClosureKind.weekly, ClosureKind.annual_span}
    weekly = next(r for r in rules if r.kind is ClosureKind.weekly)
    assert weekly.weekdays == (0,)  # 「12月29日」の日付を曜日と読み違えていない


def test_always_open_is_parsed_as_no_rules() -> None:
    rules, note = parse_closures("年中無休")
    assert rules == [] and note == "always_open"


def test_irregular_closing_is_explicit() -> None:
    """「不定休」は規則が無いのではなく、計算できないことが分かっている状態。"""
    rules, note = parse_closures("不定休")
    assert note == "irregular"
    assert rules is not None and rules[0].kind is ClosureKind.irregular


def test_unreadable_closures_stay_unparsed() -> None:
    value, note = parse_closures("公式サイトをご確認ください")
    assert value is None and note == "no_closure_rule"


# --- 運行日 -----------------------------------------------------------------


def test_service_days_for_transport() -> None:
    weekday, _ = parse_service_days("平日ダイヤ")
    assert weekday is not None
    assert weekday.weekdays == (0, 1, 2, 3, 4) and weekday.exclude_holidays

    holiday, _ = parse_service_days("土休日ダイヤ")
    assert holiday is not None
    assert holiday.weekdays == (5, 6) and holiday.include_holidays

    every, _ = parse_service_days("毎日運航")
    assert every is not None and every.every_day


def test_saturday_holiday_variants() -> None:
    for text in ("土休日", "土日祝", "土・日・祝", "土曜・休日"):
        selector = parse_day_selector(text)
        assert selector is not None and selector.include_holidays, text


# --- 期間と特定日 -----------------------------------------------------------


def test_date_range_with_and_without_year() -> None:
    span, note = parse_date_range("2026年9月15日〜9月20日", year=2026)
    assert note is None and span == (date(2026, 9, 15), date(2026, 9, 20))
    span, _ = parse_date_range("9月15日〜20日", year=2026)
    assert span == (date(2026, 9, 15), date(2026, 9, 20))
    span, _ = parse_date_range("2026/9/15〜2026/9/20", year=2026)
    assert span == (date(2026, 9, 15), date(2026, 9, 20))


def test_date_range_crossing_the_year() -> None:
    """12月29日〜1月3日 の終わりは翌年。年は呼び出し側が渡す（UTC 実行で 1 年ずれないため）。"""
    span, _ = parse_date_range("12月29日〜1月3日", year=2026)
    assert span == (date(2026, 12, 29), date(2027, 1, 3))


def test_single_date_is_a_one_day_span() -> None:
    span, note = parse_date_range("9月15日（火）", year=2026)
    assert span == (date(2026, 9, 15), date(2026, 9, 15))
    assert note == "single_day"


def test_specific_dates() -> None:
    days, _ = parse_specific_dates("9月15日・9月16日", year=2026)
    assert days == (date(2026, 9, 15), date(2026, 9, 16))


# --- 所要時間 ---------------------------------------------------------------


def test_duration() -> None:
    assert parse_minutes("約60分") == (60, "approx")
    assert parse_minutes("1時間30分") == (90, None)
    assert parse_minutes("2時間") == (120, None)
    assert parse_minutes("半日") == (None, "vague")
    assert parse_minutes("") == (None, "no_text")


# --- 季節ごとの表（寒霞渓のロープウェイで実際に出た形） ---------------------


def test_slash_dates_become_seasons() -> None:
    """「03/21~10/20 8:30~17:00」。区切りの「/」で断片が壊れないことまで確かめる。"""
    periods, note = parse_opening_hours(
        "営業時間 03/21~10/20 8:30~17:00 10/21~11/30 8:00~17:00 12/21~03/20 8:30~16:30"
    )
    assert note is None
    assert periods is not None
    assert len(periods) == 3
    spans = [(p.season.start_month, p.season.start_day) for p in periods if p.season]
    assert spans == [(3, 21), (10, 21), (12, 21)]
    assert periods[2].ranges[0].end == time(16, 30)
    # 12/21〜03/20 は年をまたぐ。冬の日がこの区分に入る
    assert periods[2].season is not None
    assert periods[2].season.contains(date(2027, 1, 5)) is True
    assert periods[0].season is not None
    assert periods[0].season.contains(date(2027, 1, 5)) is False


def test_a_season_heading_on_its_own_line_applies_to_the_rows_below() -> None:
    """表では季節が行見出しになり、時間は次の行に来る。対応を取り違えると冬の時間を夏に出す。"""
    text = "\n".join(["区分 営業時間 始発 最終便", "03/21~10/20", "8:30~17:00", "8:36", "17:00"])
    periods, note = parse_opening_hours(text)
    assert note is None
    assert periods is not None
    assert periods[0].season is not None
    assert (periods[0].season.start_month, periods[0].season.end_month) == (3, 10)
    assert periods[0].ranges[0].start == time(8, 30)


def test_a_time_without_a_season_heading_stays_unconditional() -> None:
    """見出しを持ち越すのは季節だけの断片が先に来たときに限る。"""
    periods, _ = parse_opening_hours("9:00〜17:00")
    assert periods is not None
    assert periods[0].season is None


def test_a_lone_clock_is_not_a_range() -> None:
    assert parse_opening_hours("始発 8:36") == (None, "no_time_range")


# --- 注記の曜日を本体に付けない（高松市美術館で実際に起きた） -----------------


def test_a_note_after_the_time_does_not_restrict_the_weekdays() -> None:
    """「※特別展開催期間中の金曜日・土曜日は午後7時まで」の金曜を本体に付けてはいけない。

    付けると、毎日開いている美術館が**金曜だけ開館**になり、他の曜日は「不明」になる。
    日本語の公式ページは曜日を時刻より先に書くので、時刻より後の曜日は例外や注記である。
    """
    quote = "\n".join(
        [
            "午前9時30分~午後5時",
            "※展示室への入室は閉室時間の30分前まで",
            "※特別展開催期間中の金曜日・土曜日は午後7時まで",
        ]
    )
    periods, note = parse_opening_hours(quote)
    assert note is None
    assert periods is not None and len(periods) == 1
    assert periods[0].days.every_day, periods[0].days
    assert periods[0].ranges[0].start == time(9, 30)
    assert periods[0].ranges[0].end == time(17, 0)


def test_a_weekday_before_the_time_still_restricts_it() -> None:
    periods, _ = parse_opening_hours("平日 9:00〜17:00、土日祝 9:00〜18:00")
    assert periods is not None
    assert periods[0].days.weekdays == (0, 1, 2, 3, 4)
    assert periods[1].days.include_holidays is True


def test_the_short_weekday_range_is_read() -> None:
    """営業時間では「月〜金 9:00〜17:00」と「曜」を省く。読めないと毎日開館になる。"""
    periods, _ = parse_opening_hours("月〜金 9:00〜17:00")
    assert periods is not None
    assert periods[0].days.weekdays == (0, 1, 2, 3, 4)


def test_a_month_range_is_not_read_as_weekdays() -> None:
    """「1月〜3月」の「月」を月曜と読むと、季節の指定が曜日になってしまう。"""
    assert parse_day_selector("1月〜3月") is None
    periods, _ = parse_opening_hours("3月〜9月 9:00〜17:00、10月〜2月 9:00〜16:30")
    assert periods is not None
    assert periods[0].season is not None
    assert (periods[0].season.start_month, periods[0].season.end_month) == (3, 9)
    assert periods[0].days.every_day


def test_a_shrine_precinct_that_is_freely_open_is_read() -> None:
    """寺社は「参拝自由」と書く。読めないと境内が「時間の記載なし」になる。"""
    periods, note = parse_opening_hours("参拝自由(お納経7:00~17:00)")
    assert note == "always_open"
    assert periods is not None and periods[0].always_open
    periods, note = parse_opening_hours("拝観自由")
    assert note == "always_open"
