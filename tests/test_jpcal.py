"""日本の祝日と日本時間（ADR 0018）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sitemill.clock import JST, jst_today, to_jst
from sitemill.jpcal import HolidayCalendar, computed_holidays, load_cabinet_office_csv


def test_fixed_and_happy_monday_holidays() -> None:
    h = computed_holidays(2026)
    assert h[date(2026, 1, 1)] == "元日"
    assert h[date(2026, 1, 12)] == "成人の日"  # 1 月第 2 月曜
    assert h[date(2026, 7, 20)] == "海の日"  # 7 月第 3 月曜
    assert h[date(2026, 10, 12)] == "スポーツの日"  # 10 月第 2 月曜
    assert h[date(2026, 2, 23)] == "天皇誕生日"


def test_equinoxes_are_computed() -> None:
    assert date(2026, 3, 20) in computed_holidays(2026)  # 春分の日
    assert date(2026, 9, 23) in computed_holidays(2026)  # 秋分の日
    assert date(2025, 3, 20) in computed_holidays(2025)
    assert date(2027, 9, 23) in computed_holidays(2027)


def test_substitute_holiday_skips_consecutive_holidays() -> None:
    """2026 年は 5/3（日）が憲法記念日。5/4・5/5 も祝日なので振替は 5/6 になる。"""
    h = computed_holidays(2026)
    assert date(2026, 5, 6) in h and "振替" in h[date(2026, 5, 6)]
    assert date(2026, 5, 4) in h and date(2026, 5, 5) in h


def test_national_holiday_between_two_holidays() -> None:
    """2026 年 9 月は 21 日（敬老の日）と 23 日（秋分の日）に挟まれた 22 日が国民の休日。"""
    h = computed_holidays(2026)
    assert h[date(2026, 9, 21)] == "敬老の日"
    assert date(2026, 9, 22) in h and "国民の休日" in h[date(2026, 9, 22)]
    assert h[date(2026, 9, 23)] == "秋分の日"


def test_calendar_queries() -> None:
    cal = HolidayCalendar.computed()
    assert cal.is_holiday(date(2026, 9, 21))
    assert not cal.is_holiday(date(2026, 9, 24))
    assert cal.name(date(2026, 9, 21)) == "敬老の日"
    # 3 連休のあとの最初の非祝日
    assert cal.next_non_holiday(date(2026, 9, 21)) == date(2026, 9, 24)


def test_calendar_says_when_it_cannot_answer() -> None:
    """答えられない年を黙って「祝日でない」と返すと、開館日を誤判定する。"""
    cal = HolidayCalendar.computed()
    assert cal.covers(date(2026, 1, 1))
    assert not cal.covers(date(2150, 1, 1))
    assert cal.name(date(2150, 1, 1)) is None


def test_primary_data_wins_over_computation(tmp_path: Path) -> None:
    """一次データがある年はそれを使う（一度だけの移動がある年がある）。"""
    csv = tmp_path / "syukujitsu.csv"
    csv.write_text(
        "国民の祝日・休日月日,国民の祝日・休日名称\n2026/1/1,元日\n2026/7/23,海の日\n",
        encoding="cp932",
    )
    cal = HolidayCalendar.from_csv(csv, fetched_on=date(2026, 9, 12))
    assert cal.name(date(2026, 7, 23)) == "海の日"
    assert cal.name(date(2026, 7, 20)) is None  # 一次データにその日は無い
    # 一次データに無い年は計算で埋める
    assert cal.name(date(2027, 1, 1)) == "元日"


def test_csv_without_holiday_rows_is_rejected(tmp_path: Path) -> None:
    csv = tmp_path / "broken.csv"
    csv.write_text("見出しだけ,名称\n", encoding="cp932")
    with pytest.raises(ValueError, match="読めなかった"):
        load_cabinet_office_csv(csv)


def test_load_falls_back_to_computation_when_the_file_is_missing(tmp_path: Path) -> None:
    """祝日 CSV の取得に失敗した日に祝日が消えると、休館日を開館と誤判定する。"""
    cal = HolidayCalendar.load(tmp_path / "nope.csv")
    assert cal.is_holiday(date(2026, 9, 21))


def test_merged_with_adds_service_specific_days() -> None:
    cal = HolidayCalendar.computed().merged_with([(date(2026, 9, 25), "町民の日")])
    assert cal.name(date(2026, 9, 25)) == "町民の日"
    assert cal.name(date(2026, 9, 21)) == "敬老の日"


# --- 日本時間 ---------------------------------------------------------------


def test_today_is_computed_in_jst_not_utc() -> None:
    """Actions は UTC で動く。JST の朝 6 時は UTC ではまだ前日で、判定が 1 日ずれる。"""
    utc_morning = datetime(2026, 9, 11, 21, 10, tzinfo=UTC)  # JST では 9/12 06:10
    assert utc_morning.date() == date(2026, 9, 11)
    assert jst_today(utc_morning) == date(2026, 9, 12)


def test_jst_year_boundary() -> None:
    """年末も同じ。UTC の 12/31 15:00 は JST では翌年の元日。"""
    assert jst_today(datetime(2026, 12, 31, 15, 0, tzinfo=UTC)) == date(2027, 1, 1)


def test_naive_datetimes_are_treated_as_jst() -> None:
    naive = datetime(2026, 9, 12, 6, 10)
    assert to_jst(naive).tzinfo is JST
    assert jst_today(naive) == date(2026, 9, 12)
