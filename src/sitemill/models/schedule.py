"""開館時間・定休日・運行日・臨時の告知を表すモデル（ADR 0018）。

「何時から何時まで」ではなく**規則**を持つのが要点。今日開いているかを計算するには、
「毎週月曜休館（祝日の場合は翌日）」のような規則と祝日の暦を突き合わせる必要がある。

原文の引用・一次情報 URL・取得日時は `Evidence` として各要素が持つ。判定の根拠を表示するため、
および規則と告知が食い違ったときにどちらが新しいかを決めるために使う。
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

MONDAY, SUNDAY = 0, 6
WEEKDAY_LABELS_JA = ("月", "火", "水", "木", "金", "土", "日")


class Evidence(BaseModel):
    """その要素の出どころ。原文の引用と一次情報 URL と取得日時。"""

    quote: str | None = None
    source_url: str | None = None
    fetched_at: datetime | None = None


class TimeRange(BaseModel):
    """開いている時間帯。`last_entry` は入館・受付の締切。"""

    start: time
    end: time
    last_entry: time | None = None
    note: str | None = None

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start


class DaySelector(BaseModel):
    """どの日に当てはまるか。曜日と祝日の扱いで表す。

    - `weekdays` が空なら曜日で絞らない
    - `include_holidays`: 祝日は曜日に関わらず当てはまる（「土休日」「土日祝」）
    - `exclude_holidays`: 祝日は当てはまらない（「平日（祝日を除く）」）
    """

    weekdays: tuple[int, ...] = ()
    include_holidays: bool = False
    exclude_holidays: bool = False

    @model_validator(mode="after")
    def _check(self) -> DaySelector:
        if self.include_holidays and self.exclude_holidays:
            raise ValueError("祝日を含めると同時に除くことはできない")
        if any(d < MONDAY or d > SUNDAY for d in self.weekdays):
            raise ValueError("曜日は 0（月）〜6（日）で表す")
        return self

    @property
    def every_day(self) -> bool:
        return not self.weekdays and not self.include_holidays and not self.exclude_holidays

    @property
    def depends_on_holidays(self) -> bool:
        return self.include_holidays or self.exclude_holidays

    def matches(self, day: date, *, is_holiday: bool | None = None) -> bool | None:
        """当てはまるか。祝日かどうかが必要なのに分からないときは None を返す。"""
        if self.every_day:
            return True
        if self.depends_on_holidays and is_holiday is None:
            return None
        if self.include_holidays and is_holiday:
            return True
        if self.exclude_holidays and is_holiday:
            return False
        if not self.weekdays:
            # 「祝日のみ」のような指定。祝日を含める指定で祝日でなければ当てはまらない
            return False if self.include_holidays else True
        return day.weekday() in self.weekdays

    def label_ja(self) -> str:
        parts = [WEEKDAY_LABELS_JA[d] for d in sorted(self.weekdays)]
        if self.include_holidays:
            parts.append("祝")
        text = "・".join(parts)
        if self.exclude_holidays:
            text += "（祝日を除く）"
        return text or "毎日"


class DateSpan(BaseModel):
    """年を含む期間（臨時休業の告知など）。両端を含む。"""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> DateSpan:
        if self.end < self.start:
            raise ValueError("期間の終わりが始まりより前になっている")
        return self

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class AnnualSpan(BaseModel):
    """毎年繰り返す期間（年末年始、季節の開館時間）。年をまたいでよい。

    12/29〜1/3 のように `end` が `start` より前なら、年をまたぐ期間として扱う。
    """

    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_month: int = Field(ge=1, le=12)
    end_day: int = Field(ge=1, le=31)

    @property
    def crosses_year(self) -> bool:
        return (self.end_month, self.end_day) < (self.start_month, self.start_day)

    def contains(self, day: date) -> bool:
        here = (day.month, day.day)
        start = (self.start_month, self.start_day)
        end = (self.end_month, self.end_day)
        if self.crosses_year:
            return here >= start or here <= end
        return start <= here <= end

    def label_ja(self) -> str:
        return f"{self.start_month}月{self.start_day}日〜{self.end_month}月{self.end_day}日"


class HoursPeriod(BaseModel):
    """ある条件の日に当てはまる開館時間。"""

    ranges: list[TimeRange] = Field(default_factory=list)
    days: DaySelector = Field(default_factory=DaySelector)
    season: AnnualSpan | None = None
    label: str | None = None
    evidence: Evidence | None = None

    def applies_to(self, day: date, *, is_holiday: bool | None = None) -> bool | None:
        if self.season is not None and not self.season.contains(day):
            return False
        return self.days.matches(day, is_holiday=is_holiday)


class ClosureKind(StrEnum):
    weekly = "weekly"  # 毎週<曜日>
    nth_weekday = "nth_weekday"  # 第 n <曜日>
    annual_span = "annual_span"  # 毎年の期間（年末年始など）
    dates = "dates"  # 特定の日
    irregular = "irregular"  # 不定休。計算できないことを明示するための種別


class HolidayBehavior(StrEnum):
    """定休日が祝日に重なったときの扱い。原文の注記から決める。"""

    closed = "closed"  # 祝日でも休む（注記なし＝規則どおり）
    open = "open"  # 祝日は開ける（振替の休館日は無い）
    next_day = "next_day"  # 祝日なら「翌日」休む
    next_weekday = "next_weekday"  # 祝日なら「翌平日」休む
    unspecified = "unspecified"  # 祝日の注記はあるが、どう動くか原文から決められない


class ClosureRule(BaseModel):
    """休館日・運休日の規則。"""

    kind: ClosureKind
    weekdays: tuple[int, ...] = ()
    nths: tuple[int, ...] = ()  # 第 n 週（nth_weekday のとき）
    annual: AnnualSpan | None = None
    dates: tuple[date, ...] = ()
    holiday_behavior: HolidayBehavior = HolidayBehavior.closed
    label: str | None = None
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def _check(self) -> ClosureRule:
        if self.kind in (ClosureKind.weekly, ClosureKind.nth_weekday) and not self.weekdays:
            raise ValueError(f"{self.kind} には weekdays が必要")
        if self.kind is ClosureKind.nth_weekday and not self.nths:
            raise ValueError("nth_weekday には nths（第何週か）が必要")
        if self.kind is ClosureKind.annual_span and self.annual is None:
            raise ValueError("annual_span には annual が必要")
        if self.kind is ClosureKind.dates and not self.dates:
            raise ValueError("dates には日付が必要")
        return self

    @property
    def moves_on_holiday(self) -> bool:
        return self.holiday_behavior in (
            HolidayBehavior.next_day,
            HolidayBehavior.next_weekday,
        )


class NoticeKind(StrEnum):
    closed = "closed"  # 臨時休業・運休
    open = "open"  # 臨時開館・臨時運航
    schedule_change = "schedule_change"  # 時間や本数の変更（開閉の判定には使わない）


class SpecialNotice(BaseModel):
    """告知（お知らせページ由来）。規則より新しければ規則に優先する。"""

    kind: NoticeKind
    span: DateSpan
    reason: str | None = None
    evidence: Evidence | None = None

    @property
    def fetched_at(self) -> datetime | None:
        return self.evidence.fetched_at if self.evidence else None
