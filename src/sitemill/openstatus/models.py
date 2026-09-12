"""判定の結果と根拠（ADR 0018）。

状態は open / closed / unknown の 3 値だけ。「たぶん開いている」は作らない。
断定できないときに断定すると、利用者は休館日に島まで渡ることになる。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from sitemill.models.schedule import Evidence, TimeRange


class DayState(StrEnum):
    open = "open"
    closed = "closed"
    unknown = "unknown"


class ReasonCode(StrEnum):
    """判定の根拠。表示の文言はサービスがロケールごとに用意する。"""

    regular_hours = "regular_hours"  # 通常の開館時間
    always_open = "always_open"  # 時間の指定が無い（入園自由と原文が明示）
    weekly_closed = "weekly_closed"  # 毎週の定休日
    nth_weekday_closed = "nth_weekday_closed"  # 第 n 曜日の定休日
    annual_closed = "annual_closed"  # 年末年始などの期間
    date_closed = "date_closed"  # 特定日の休み
    holiday_open_exception = "holiday_open_exception"  # 定休日だが祝日なので開ける
    substitute_closed = "substitute_closed"  # 祝日の振替で休み
    substitute_ambiguous = "substitute_ambiguous"  # 振替先が原文から決められない
    irregular_closed = "irregular_closed"  # 不定休
    not_in_service = "not_in_service"  # その日は運行しないダイヤ
    special_closure_notice = "special_closure_notice"  # 臨時休業の告知
    special_open_notice = "special_open_notice"  # 臨時開館の告知
    conflicting = "conflicting"  # 規則と告知、または告知同士が食い違う
    stale_source = "stale_source"  # 一次情報の取得が途切れている
    holiday_unknown = "holiday_unknown"  # その年の祝日が分からない
    no_data = "no_data"  # 材料が無い


# unknown にしか結びつかない根拠。これが付いたら state は unknown。
UNCERTAIN_CODES = frozenset(
    {
        ReasonCode.substitute_ambiguous,
        ReasonCode.irregular_closed,
        ReasonCode.conflicting,
        ReasonCode.stale_source,
        ReasonCode.holiday_unknown,
        ReasonCode.no_data,
    }
)


class Reason(BaseModel):
    """根拠 1 件。原文の引用と一次情報 URL と取得日時を添えて表示する。"""

    code: ReasonCode
    detail: str = ""  # 人が読む短い補足（規則のラベル、祝日名など）
    evidence: Evidence | None = None

    @property
    def quote(self) -> str | None:
        return self.evidence.quote if self.evidence else None

    @property
    def source_url(self) -> str | None:
        return self.evidence.source_url if self.evidence else None

    @property
    def fetched_at(self) -> datetime | None:
        return self.evidence.fetched_at if self.evidence else None


class DayVerdict(BaseModel):
    """ある 1 日についての判定。"""

    day: date
    state: DayState
    periods: list[TimeRange] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state is DayState.open

    @property
    def is_unknown(self) -> bool:
        return self.state is DayState.unknown

    def has(self, code: ReasonCode) -> bool:
        return any(r.code is code for r in self.reasons)

    @property
    def codes(self) -> tuple[ReasonCode, ...]:
        return tuple(r.code for r in self.reasons)
