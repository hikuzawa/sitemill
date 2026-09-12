"""「その日、開いているか」の判定（ADR 0018）。境界の扱いをここで固定する。

基準にする 2026 年 9 月の暦（計算・一次データとも一致）:
    9/19(土) 9/20(日) 9/21(月・敬老の日) 9/22(火・国民の休日) 9/23(水・秋分の日) 9/24(木)
祝日が 3 日続くため、「祝日の場合は翌日休館」の振替先がまた祝日になる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sitemill.jpcal import HolidayCalendar
from sitemill.models.schedule import (
    AnnualSpan,
    ClosureKind,
    ClosureRule,
    DateSpan,
    DaySelector,
    Evidence,
    HolidayBehavior,
    HoursPeriod,
    NoticeKind,
    SpecialNotice,
    TimeRange,
)
from sitemill.openstatus import DayState, ReasonCode, resolve_day, resolve_week

CAL = HolidayCalendar.computed()
TEN_TO_FIVE = [
    HoursPeriod(ranges=[TimeRange(start=time(10, 0), end=time(17, 0), last_entry=time(16, 30))])
]


def _monday(behavior: HolidayBehavior, *, fetched_at: datetime | None = None) -> ClosureRule:
    return ClosureRule(
        kind=ClosureKind.weekly,
        weekdays=(0,),
        holiday_behavior=behavior,
        label="毎週月曜休館",
        evidence=Evidence(
            quote="毎週月曜日（祝日の場合は翌日）",
            source_url="https://museum.example/hours/",
            fetched_at=fetched_at,
        ),
    )


def _resolve(day: date, **kwargs: object) -> object:
    kwargs.setdefault("hours", TEN_TO_FIVE)
    kwargs.setdefault("holidays", CAL)
    return resolve_day(day, **kwargs)  # type: ignore[arg-type]


# --- 通常の日 ---------------------------------------------------------------


def test_open_day_reports_hours_and_last_entry() -> None:
    v = _resolve(date(2026, 9, 12), closures=[_monday(HolidayBehavior.next_day)])
    assert v.state is DayState.open
    assert v.periods[0].start == time(10, 0) and v.periods[0].last_entry == time(16, 30)
    assert v.has(ReasonCode.regular_hours)


def test_weekly_closing_day() -> None:
    v = _resolve(date(2026, 9, 14), closures=[_monday(HolidayBehavior.closed)])
    assert v.state is DayState.closed and v.periods == []
    assert v.has(ReasonCode.weekly_closed)
    assert v.reasons[0].quote == "毎週月曜日（祝日の場合は翌日）"
    assert v.reasons[0].source_url == "https://museum.example/hours/"


def test_no_data_is_unknown_not_open() -> None:
    v = resolve_day(date(2026, 9, 12), holidays=CAL)
    assert v.state is DayState.unknown and v.has(ReasonCode.no_data)


def test_open_but_no_hours_for_that_day_is_unknown() -> None:
    """休館日ではないと分かっても、その日の開館時間が無ければ「開いている」と言わない。"""
    summer = HoursPeriod(
        ranges=[TimeRange(start=time(9, 0), end=time(17, 0))],
        season=AnnualSpan(start_month=7, start_day=1, end_month=8, end_day=31),
    )
    v = resolve_day(date(2026, 9, 12), hours=[summer], holidays=CAL)
    assert v.state is DayState.unknown and v.has(ReasonCode.no_data)


# --- 祝日と振替（連続する祝日）----------------------------------------------


def test_holiday_on_a_closing_day_opens_it() -> None:
    """9/21（月）は敬老の日。「祝日の場合は翌日」なので当日は開館する。"""
    v = _resolve(date(2026, 9, 21), closures=[_monday(HolidayBehavior.next_day)])
    assert v.state is DayState.open
    assert v.has(ReasonCode.holiday_open_exception)
    assert "敬老の日" in next(
        r.detail for r in v.reasons if r.code is ReasonCode.holiday_open_exception
    )


def test_next_day_substitute_is_unknown_when_the_next_day_is_also_a_holiday() -> None:
    """9/22 は国民の休日。「翌日休館」の振替先がまた祝日で、原文からは決められない。"""
    v = _resolve(date(2026, 9, 22), closures=[_monday(HolidayBehavior.next_day)])
    assert v.state is DayState.unknown
    assert v.has(ReasonCode.substitute_ambiguous)
    detail = next(r.detail for r in v.reasons if r.code is ReasonCode.substitute_ambiguous)
    assert "敬老の日" in detail and "国民の休日" in detail


def test_next_weekday_substitute_lands_after_the_run_of_holidays() -> None:
    """「祝日の場合は翌平日」なら決められる。9/21 の振替は 9/24（木）。"""
    rule = _monday(HolidayBehavior.next_weekday)
    assert _resolve(date(2026, 9, 21), closures=[rule]).state is DayState.open
    assert _resolve(date(2026, 9, 22), closures=[rule]).state is DayState.open
    assert _resolve(date(2026, 9, 23), closures=[rule]).state is DayState.open
    thursday = _resolve(date(2026, 9, 24), closures=[rule])
    assert thursday.state is DayState.closed
    assert thursday.has(ReasonCode.substitute_closed)
    assert "敬老の日" in next(
        r.detail for r in thursday.reasons if r.code is ReasonCode.substitute_closed
    )


def test_next_day_substitute_in_an_ordinary_week() -> None:
    """祝日が続かない週なら「翌日休館」は決まる。2026/7/20（月・海の日）→ 7/21（火）。"""
    rule = _monday(HolidayBehavior.next_day)
    assert _resolve(date(2026, 7, 20), closures=[rule]).state is DayState.open
    tuesday = _resolve(date(2026, 7, 21), closures=[rule])
    assert tuesday.state is DayState.closed and tuesday.has(ReasonCode.substitute_closed)


def test_holiday_open_rule_has_no_substitute() -> None:
    """「祝日は開館」だけの規則では、振替の休館日は生まれない。"""
    rule = _monday(HolidayBehavior.open)
    assert _resolve(date(2026, 7, 20), closures=[rule]).state is DayState.open
    assert _resolve(date(2026, 7, 21), closures=[rule]).state is DayState.open


def test_unspecified_holiday_note_is_unknown() -> None:
    v = _resolve(date(2026, 9, 21), closures=[_monday(HolidayBehavior.unspecified)])
    assert v.state is DayState.unknown and v.has(ReasonCode.substitute_ambiguous)


def test_unknown_holiday_year_is_unknown() -> None:
    """祝日を答えられない年は、規則だけで断定しない。"""
    v = resolve_day(
        date(2150, 9, 21),
        hours=TEN_TO_FIVE,
        closures=[_monday(HolidayBehavior.next_day)],
        holidays=CAL,
    )
    assert v.state is DayState.unknown and v.has(ReasonCode.holiday_unknown)


# --- 年末年始（年をまたぐ期間）----------------------------------------------


def test_year_end_span_closes_both_sides_of_the_new_year() -> None:
    rule = ClosureRule(
        kind=ClosureKind.annual_span,
        annual=AnnualSpan(start_month=12, start_day=29, end_month=1, end_day=3),
        label="年末年始",
    )
    for day in (date(2026, 12, 29), date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 3)):
        v = _resolve(day, closures=[rule])
        assert v.state is DayState.closed, day
        assert v.has(ReasonCode.annual_closed)
    for day in (date(2026, 12, 28), date(2027, 1, 4)):
        assert _resolve(day, closures=[rule]).state is DayState.open, day


# --- 第 n 曜日（第 5 週がある月）--------------------------------------------


def test_nth_weekday_closures_and_the_fifth_week() -> None:
    """2026 年 9 月の水曜は 2・9・16・23・30 の 5 回。第 2・第 4 は 9 日と 23 日。"""
    rule = ClosureRule(
        kind=ClosureKind.nth_weekday,
        weekdays=(2,),
        nths=(2, 4),
        label="第2・第4水曜休館",
    )
    assert _resolve(date(2026, 9, 9), closures=[rule]).state is DayState.closed
    assert _resolve(date(2026, 9, 23), closures=[rule]).state is DayState.closed
    assert _resolve(date(2026, 9, 2), closures=[rule]).state is DayState.open
    assert _resolve(date(2026, 9, 16), closures=[rule]).state is DayState.open
    # 第 5 水曜は指定に無いので開館
    fifth = _resolve(date(2026, 9, 30), closures=[rule])
    assert fifth.state is DayState.open and not fifth.has(ReasonCode.nth_weekday_closed)


def test_nth_weekday_with_a_holiday_substitute() -> None:
    """第 4 水曜の 9/23 は秋分の日。「祝日の場合は翌日」なら 9/24 が休館。"""
    rule = ClosureRule(
        kind=ClosureKind.nth_weekday,
        weekdays=(2,),
        nths=(2, 4),
        holiday_behavior=HolidayBehavior.next_day,
        label="第2・第4水曜休館（祝日の場合は翌日）",
    )
    assert _resolve(date(2026, 9, 23), closures=[rule]).state is DayState.open
    thursday = _resolve(date(2026, 9, 24), closures=[rule])
    assert thursday.state is DayState.closed and thursday.has(ReasonCode.substitute_closed)


# --- 不定休 -----------------------------------------------------------------


def test_irregular_closing_is_unknown() -> None:
    rule = ClosureRule(kind=ClosureKind.irregular, label="不定休")
    v = _resolve(date(2026, 9, 12), closures=[rule])
    assert v.state is DayState.unknown and v.has(ReasonCode.irregular_closed)


# --- 交通の運行日 -----------------------------------------------------------


def test_saturday_holiday_timetable_includes_holidays() -> None:
    """「土休日ダイヤ」は 9/21（月・敬老の日）にも運行する。曜日だけで見ると取り違える。"""
    weekend = DaySelector(weekdays=(5, 6), include_holidays=True)
    ferry = [HoursPeriod(ranges=[TimeRange(start=time(7, 0), end=time(19, 0))])]
    assert (
        resolve_day(date(2026, 9, 21), hours=ferry, service_days=weekend, holidays=CAL).state
        is DayState.open
    )
    assert (
        resolve_day(date(2026, 9, 19), hours=ferry, service_days=weekend, holidays=CAL).state
        is DayState.open
    )
    # 平日（祝日でない木曜）は運行しない
    thursday = resolve_day(date(2026, 9, 24), hours=ferry, service_days=weekend, holidays=CAL)
    assert thursday.state is DayState.closed and thursday.has(ReasonCode.not_in_service)


def test_weekday_timetable_excludes_holidays() -> None:
    """「平日ダイヤ」は祝日には運行しない。9/21 は月曜だが祝日。"""
    weekday = DaySelector(weekdays=(0, 1, 2, 3, 4), exclude_holidays=True)
    bus = [HoursPeriod(ranges=[TimeRange(start=time(6, 0), end=time(20, 0))])]
    holiday = resolve_day(date(2026, 9, 21), hours=bus, service_days=weekday, holidays=CAL)
    assert holiday.state is DayState.closed and holiday.has(ReasonCode.not_in_service)
    assert (
        resolve_day(date(2026, 9, 24), hours=bus, service_days=weekday, holidays=CAL).state
        is DayState.open
    )


def test_service_days_unknown_when_holidays_are_unknown() -> None:
    weekend = DaySelector(weekdays=(5, 6), include_holidays=True)
    v = resolve_day(date(2150, 9, 21), hours=TEN_TO_FIVE, service_days=weekend, holidays=CAL)
    assert v.state is DayState.unknown and v.has(ReasonCode.holiday_unknown)


# --- 告知と規則の食い違い ---------------------------------------------------

RULE_AT = datetime(2026, 9, 12, 6, 10, tzinfo=UTC)
NEWER = datetime(2026, 9, 12, 6, 20, tzinfo=UTC)
OLDER = datetime(2026, 9, 5, 6, 10, tzinfo=UTC)


def _notice(kind: NoticeKind, start: date, end: date, at: datetime | None) -> SpecialNotice:
    return SpecialNotice(
        kind=kind,
        span=DateSpan(start=start, end=end),
        reason="設備点検",
        evidence=Evidence(
            quote="9月15日から9月20日まで臨時休館します",
            source_url="https://museum.example/news/1",
            fetched_at=at,
        ),
    )


def test_closure_notice_closes_a_normally_open_day() -> None:
    v = _resolve(
        date(2026, 9, 16),
        closures=[_monday(HolidayBehavior.closed, fetched_at=RULE_AT)],
        notices=[_notice(NoticeKind.closed, date(2026, 9, 15), date(2026, 9, 20), NEWER)],
    )
    assert v.state is DayState.closed
    assert v.has(ReasonCode.special_closure_notice)
    assert v.reasons[0].source_url == "https://museum.example/news/1"


def test_newer_open_notice_beats_the_closing_rule_and_keeps_both_reasons() -> None:
    """臨時開館の告知が規則より新しければ告知を優先する。根拠は両方残す。"""
    v = _resolve(
        date(2026, 9, 14),  # 月曜＝定休日
        closures=[_monday(HolidayBehavior.closed, fetched_at=RULE_AT)],
        notices=[_notice(NoticeKind.open, date(2026, 9, 14), date(2026, 9, 14), NEWER)],
    )
    assert v.state is DayState.open
    assert v.has(ReasonCode.special_open_notice) and v.has(ReasonCode.weekly_closed)
    assert v.has(ReasonCode.conflicting)


def test_older_open_notice_does_not_beat_the_rule() -> None:
    """告知が規則より古ければ、どちらが正しいか決められないので unknown。"""
    v = _resolve(
        date(2026, 9, 14),
        closures=[_monday(HolidayBehavior.closed, fetched_at=RULE_AT)],
        notices=[_notice(NoticeKind.open, date(2026, 9, 14), date(2026, 9, 14), OLDER)],
    )
    assert v.state is DayState.unknown
    assert v.has(ReasonCode.conflicting)
    assert v.has(ReasonCode.weekly_closed) and v.has(ReasonCode.special_open_notice)


def test_notices_that_contradict_each_other_are_unknown() -> None:
    v = _resolve(
        date(2026, 9, 16),
        notices=[
            _notice(NoticeKind.closed, date(2026, 9, 15), date(2026, 9, 20), NEWER),
            _notice(NoticeKind.open, date(2026, 9, 16), date(2026, 9, 16), NEWER),
        ],
    )
    assert v.state is DayState.unknown and v.has(ReasonCode.conflicting)


def test_schedule_change_notice_does_not_decide_open_or_closed() -> None:
    notice = SpecialNotice(
        kind=NoticeKind.schedule_change,
        span=DateSpan(start=date(2026, 9, 16), end=date(2026, 9, 16)),
        reason="時間変更",
    )
    v = _resolve(date(2026, 9, 16), closures=[_monday(HolidayBehavior.closed)], notices=[notice])
    assert v.state is DayState.open


# --- 鮮度の下限 -------------------------------------------------------------


def test_stale_source_falls_back_to_unknown_but_keeps_the_rule_reasons() -> None:
    """取得が途切れていれば、規則の上で開館日でも unknown に落とす（ADR 0004）。"""
    now = datetime(2026, 9, 30, 6, 10, tzinfo=UTC)
    v = _resolve(
        date(2026, 9, 30),
        closures=[_monday(HolidayBehavior.closed)],
        fetched_at=datetime(2026, 9, 10, 6, 10, tzinfo=UTC),
        now=now,
        stale_after_days=14,
    )
    assert v.state is DayState.unknown and v.periods == []
    assert v.reasons[0].code is ReasonCode.stale_source
    assert "14 日" in v.reasons[0].detail
    assert v.has(ReasonCode.regular_hours)  # 規則の判断も根拠として残る


def test_fresh_source_is_not_marked_stale() -> None:
    now = datetime(2026, 9, 30, 6, 10, tzinfo=UTC)
    v = _resolve(
        date(2026, 9, 30),
        closures=[_monday(HolidayBehavior.closed)],
        fetched_at=datetime(2026, 9, 29, 6, 10, tzinfo=UTC),
        now=now,
        stale_after_days=14,
    )
    assert v.state is DayState.open and not v.has(ReasonCode.stale_source)


def test_never_fetched_counts_as_stale() -> None:
    v = _resolve(
        date(2026, 9, 30),
        closures=[_monday(HolidayBehavior.closed)],
        fetched_at=None,
        now=datetime(2026, 9, 30, 6, 10, tzinfo=UTC),
        stale_after_days=14,
    )
    assert v.state is DayState.unknown and v.has(ReasonCode.stale_source)


# --- 週の帯 -----------------------------------------------------------------


def test_resolve_week_covers_the_silver_week() -> None:
    """ページに出す 7 日分の帯。9/21 は開館、9/22 は不明、9/23 は開館。"""
    days = resolve_week(
        date(2026, 9, 19),
        hours=TEN_TO_FIVE,
        closures=[_monday(HolidayBehavior.next_day)],
        holidays=CAL,
    )
    assert [d.day for d in days][0] == date(2026, 9, 19)
    states = {d.day: d.state for d in days}
    assert states[date(2026, 9, 21)] is DayState.open
    assert states[date(2026, 9, 22)] is DayState.unknown
    assert states[date(2026, 9, 23)] is DayState.open
    assert states[date(2026, 9, 25)] is DayState.open
