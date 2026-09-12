"""「その日、開いているか」の計算（ADR 0018）。

判定の材料と優先順位:
  1. 臨時の告知（SpecialNotice）— 日付が当日を含むもの
  2. 定休日の規則（ClosureRule）— 曜日・第 n 曜日・年間の期間・特定日、祝日時の扱いつき
  3. 開館時間（HoursPeriod）— 曜日・季節で切り替わる

規則と告知が食い違ったときは、**取得が新しい方**を採る。同じか告知の方が古ければ unknown。
どちらの根拠も残すので、ページには「規則では開館日だが臨時休館の告知がある」と書ける。

日付はすべて `date`（時刻帯を持たない）で受け取る。「今日」を決めるのは呼び出し側の責任で、
必ず `sitemill.clock.jst_today()` を使う。実行環境が UTC だと JST の朝に前日を判定してしまう。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sitemill.clock import to_jst
from sitemill.jpcal import HolidayCalendar
from sitemill.models.schedule import (
    ClosureKind,
    ClosureRule,
    DaySelector,
    HolidayBehavior,
    HoursPeriod,
    NoticeKind,
    SpecialNotice,
    TimeRange,
)
from sitemill.openstatus.models import DayState, DayVerdict, Reason, ReasonCode

# 振替の休館日を探すときに遡る日数。週 1 の定休日なら 7 日で足りる
_LOOKBACK_DAYS = 8
# 「翌平日」を探すときに進む日数の上限
_LOOKAHEAD_DAYS = 10

_CLOSURE_CODES = {
    ClosureKind.weekly: ReasonCode.weekly_closed,
    ClosureKind.nth_weekday: ReasonCode.nth_weekday_closed,
    ClosureKind.annual_span: ReasonCode.annual_closed,
    ClosureKind.dates: ReasonCode.date_closed,
}


def nth_of_weekday(day: date) -> int:
    """その日がその月の第何週の曜日か（1 日始まりで数える）。"""
    return (day.day - 1) // 7 + 1


def next_business_day(holidays: HolidayCalendar, start: date) -> date | None:
    """start 以降で最初の「平日（土日でも祝日でもない日）」。分からなければ None。"""
    cursor = start
    for _ in range(_LOOKAHEAD_DAYS):
        if not holidays.covers(cursor):
            return None
        if cursor.weekday() < 5 and not holidays.is_holiday(cursor):
            return cursor
        cursor += timedelta(days=1)
    return None


def _matches_weekday_rule(rule: ClosureRule, day: date) -> bool:
    """曜日の条件（第 n 週の指定を含む）に当てはまるか。祝日は見ない。"""
    if day.weekday() not in rule.weekdays:
        return False
    if rule.kind is ClosureKind.nth_weekday:
        return nth_of_weekday(day) in rule.nths
    return True


def _reason(rule: ClosureRule, code: ReasonCode, detail: str | None = None) -> Reason:
    return Reason(code=code, detail=detail or rule.label or "", evidence=rule.evidence)


def _rule_on_its_own_day(
    rule: ClosureRule, day: date, holidays: HolidayCalendar
) -> tuple[DayState | None, Reason | None]:
    """規則の当たり日について、休みになるか・祝日で開けるかを決める。"""
    if rule.holiday_behavior is HolidayBehavior.closed:
        return DayState.closed, _reason(rule, _CLOSURE_CODES[rule.kind])
    if not holidays.covers(day):
        return DayState.unknown, _reason(
            rule, ReasonCode.holiday_unknown, f"{day.year} 年の祝日が分からない"
        )
    name = holidays.name(day)
    if name is None:  # 祝日でなければ注記に関わらず規則どおり休み
        return DayState.closed, _reason(rule, _CLOSURE_CODES[rule.kind])
    if rule.holiday_behavior is HolidayBehavior.unspecified:
        return DayState.unknown, _reason(
            rule, ReasonCode.substitute_ambiguous, f"{name}。祝日の扱いが原文から決められない"
        )
    # open / next_day / next_weekday はいずれも「当日は開ける」
    return DayState.open, _reason(rule, ReasonCode.holiday_open_exception, name)


def _rule_as_substitute(
    rule: ClosureRule, day: date, holidays: HolidayCalendar
) -> tuple[DayState | None, Reason | None]:
    """その日が「祝日の振替」で休みになるかを決める。

    next_day: 前日が規則の当たり日かつ祝日なら、その振替は当日。ただし当日も祝日なら、
      さらに動くのか当日休むのかは原文から決められないので unknown にする。
    next_weekday: 規則の当たり日が祝日だったとき、その後の最初の平日が休館日になる。
    """
    if not rule.moves_on_holiday:
        return None, None
    if not holidays.covers(day):
        return DayState.unknown, _reason(
            rule, ReasonCode.holiday_unknown, f"{day.year} 年の祝日が分からない"
        )

    if rule.holiday_behavior is HolidayBehavior.next_day:
        previous = day - timedelta(days=1)
        if not _matches_weekday_rule(rule, previous) or not holidays.is_holiday(previous):
            return None, None
        moved_from = holidays.name(previous)
        if holidays.is_holiday(day):
            return DayState.unknown, _reason(
                rule,
                ReasonCode.substitute_ambiguous,
                f"{moved_from}の翌日だが当日（{holidays.name(day)}）も祝日。"
                "さらに翌日へ動くのか当日休むのか原文から決められない",
            )
        return DayState.closed, _reason(rule, ReasonCode.substitute_closed, f"{moved_from}の振替")

    # next_weekday: 直近の「規則の当たり日かつ祝日」から最初の平日を探す
    for back in range(1, _LOOKBACK_DAYS):
        candidate = day - timedelta(days=back)
        if not _matches_weekday_rule(rule, candidate):
            continue
        if not holidays.covers(candidate) or not holidays.is_holiday(candidate):
            return None, None
        target = next_business_day(holidays, candidate + timedelta(days=1))
        if target is None:
            return DayState.unknown, _reason(
                rule, ReasonCode.substitute_ambiguous, "振替先の平日が決められない"
            )
        if target == day:
            return DayState.closed, _reason(
                rule, ReasonCode.substitute_closed, f"{holidays.name(candidate)}の振替"
            )
        return None, None
    return None, None


def evaluate_closures(
    rules: Sequence[ClosureRule], day: date, holidays: HolidayCalendar
) -> tuple[DayState, list[Reason]]:
    """定休日の規則だけで、その日の状態を決める。

    closed が 1 つでもあれば closed。closed が無く unknown があれば unknown。
    どれにも当たらなければ open（＝規則の上では休みではない）。
    """
    reasons: list[Reason] = []
    closed: list[Reason] = []
    uncertain: list[Reason] = []
    exceptions: list[Reason] = []

    for rule in rules:
        if rule.kind is ClosureKind.irregular:
            uncertain.append(_reason(rule, ReasonCode.irregular_closed, "不定休"))
            continue
        if rule.kind is ClosureKind.annual_span:
            if rule.annual is not None and rule.annual.contains(day):
                closed.append(_reason(rule, ReasonCode.annual_closed, rule.annual.label_ja()))
            continue
        if rule.kind is ClosureKind.dates:
            if day in rule.dates:
                closed.append(_reason(rule, ReasonCode.date_closed))
            continue
        # weekly / nth_weekday
        if _matches_weekday_rule(rule, day):
            state, reason = _rule_on_its_own_day(rule, day, holidays)
            if reason is not None:
                (
                    closed
                    if state is DayState.closed
                    else uncertain
                    if state is DayState.unknown
                    else exceptions
                ).append(reason)
            continue
        state, reason = _rule_as_substitute(rule, day, holidays)
        if reason is not None:
            (closed if state is DayState.closed else uncertain).append(reason)

    if closed:
        return DayState.closed, closed + exceptions
    if uncertain:
        return DayState.unknown, uncertain + exceptions
    reasons.extend(exceptions)
    return DayState.open, reasons


def _notice_state(
    notices: Sequence[SpecialNotice], day: date
) -> tuple[DayState | None, list[Reason], datetime | None]:
    """当日に掛かる告知から状態を決める。返り値は (状態, 根拠, 最新の取得日時)。"""
    hits = [n for n in notices if n.span.contains(day) and n.kind is not NoticeKind.schedule_change]
    if not hits:
        return None, [], None
    closed = [n for n in hits if n.kind is NoticeKind.closed]
    opened = [n for n in hits if n.kind is NoticeKind.open]
    reasons = [
        Reason(
            code=(
                ReasonCode.special_closure_notice
                if n.kind is NoticeKind.closed
                else ReasonCode.special_open_notice
            ),
            detail=n.reason or "",
            evidence=n.evidence,
        )
        for n in hits
    ]
    latest = max((n.fetched_at for n in hits if n.fetched_at is not None), default=None)
    if closed and opened:
        # 同じ日に休業と開館の告知が両方ある。新しい方を採る
        newest_closed = max((n.fetched_at for n in closed if n.fetched_at), default=None)
        newest_open = max((n.fetched_at for n in opened if n.fetched_at), default=None)
        if newest_closed is None or newest_open is None or newest_closed == newest_open:
            reasons.append(Reason(code=ReasonCode.conflicting, detail="告知同士が食い違っている"))
            return DayState.unknown, reasons, latest
        state = DayState.closed if newest_closed > newest_open else DayState.open
        return state, reasons, latest
    return (DayState.closed if closed else DayState.open), reasons, latest


def _rule_fetched_at(rules: Sequence[ClosureRule], hours: Sequence[HoursPeriod]) -> datetime | None:
    stamps = [
        item.evidence.fetched_at
        for item in (*rules, *hours)
        if item.evidence is not None and item.evidence.fetched_at is not None
    ]
    return max(stamps) if stamps else None


def periods_for(
    hours: Sequence[HoursPeriod], day: date, holidays: HolidayCalendar
) -> tuple[list[TimeRange], bool]:
    """その日に当てはまる時間帯。返り値は (時間帯, 祝日が分からず決められなかったか)。"""
    is_holiday: bool | None = holidays.name(day) is not None if holidays.covers(day) else None
    ranges: list[TimeRange] = []
    undecided = False
    for period in hours:
        applies = period.applies_to(day, is_holiday=is_holiday)
        if applies is None:
            undecided = True
            continue
        if applies:
            ranges.extend(period.ranges)
    return ranges, undecided


def resolve_day(
    day: date,
    *,
    hours: Sequence[HoursPeriod] = (),
    closures: Sequence[ClosureRule] = (),
    notices: Sequence[SpecialNotice] = (),
    service_days: DaySelector | None = None,
    holidays: HolidayCalendar | None = None,
    fetched_at: datetime | None = None,
    now: datetime | None = None,
    stale_after_days: int | None = None,
) -> DayVerdict:
    """その日の状態と根拠を返す。

    `service_days` は交通の運行日（「土休日ダイヤ」など）。当てはまらない日は運行しない。
    `fetched_at` はその対象の一次情報を最後に取得できた時刻。`stale_after_days` を超えていれば、
    規則の上で開館日でも unknown に落とす（鮮度の下限。japan-open-today ADR 0004）。
    """
    holidays = holidays or HolidayCalendar.computed()
    reasons: list[Reason] = []

    # 1. 運行日（交通）。ダイヤに無い日はそれ以上見ない
    if service_days is not None:
        is_holiday: bool | None = holidays.name(day) is not None if holidays.covers(day) else None
        matches = service_days.matches(day, is_holiday=is_holiday)
        if matches is None:
            reasons.append(
                Reason(
                    code=ReasonCode.holiday_unknown,
                    detail=f"{day.year} 年の祝日が分からないため運行日を決められない",
                )
            )
            return _finish(day, DayState.unknown, [], reasons, fetched_at, now, stale_after_days)
        if not matches:
            reasons.append(
                Reason(
                    code=ReasonCode.not_in_service,
                    detail=f"運行日は {service_days.label_ja()}",
                )
            )
            return _finish(day, DayState.closed, [], reasons, fetched_at, now, stale_after_days)

    # 2. 規則
    rule_state, rule_reasons = (
        evaluate_closures(closures, day, holidays) if closures else (DayState.open, [])
    )
    # 3. 告知
    notice_state, notice_reasons, notice_at = _notice_state(notices, day)

    state = rule_state
    reasons += rule_reasons + notice_reasons

    if notice_state is not None:
        if notice_state is DayState.unknown:
            state = DayState.unknown
        elif notice_state is DayState.closed:
            # 臨時休業は、規則の上で開館日でも休みにする（食い違いではなく追加の事実）
            state = DayState.closed
        elif rule_state is DayState.closed:
            # 臨時開館 × 定休日。新しい方を採る
            rule_at = _rule_fetched_at(closures, hours)
            if notice_at is not None and (rule_at is None or notice_at > rule_at):
                state = DayState.open
                reasons.append(
                    Reason(code=ReasonCode.conflicting, detail="告知が定休日の規則より新しい")
                )
            else:
                state = DayState.unknown
                reasons.append(
                    Reason(
                        code=ReasonCode.conflicting,
                        detail="臨時開館の告知と定休日の規則が食い違い、どちらが新しいか決められない",
                    )
                )
        else:
            state = DayState.open

    # 4. 開館時間
    periods: list[TimeRange] = []
    if state is DayState.open:
        periods, undecided = periods_for(hours, day, holidays)
        if undecided and not periods:
            state = DayState.unknown
            reasons.append(
                Reason(
                    code=ReasonCode.holiday_unknown,
                    detail=f"{day.year} 年の祝日が分からないため開館時間を決められない",
                )
            )
        elif not periods:
            state = DayState.unknown
            reasons.append(
                Reason(code=ReasonCode.no_data, detail="その日に当てはまる開館時間が無い")
            )
        else:
            reasons.insert(0, Reason(code=ReasonCode.regular_hours))
    elif not closures and not notices and not hours:
        state = DayState.unknown
        reasons.append(Reason(code=ReasonCode.no_data, detail="開館時間・休館日の情報が無い"))

    return _finish(day, state, periods, reasons, fetched_at, now, stale_after_days)


def is_stale(fetched_at: datetime | None, *, now: datetime | None = None, days: int | None) -> bool:
    """一次情報の取得が途切れているか（鮮度の下限）。"""
    if days is None:
        return False
    if fetched_at is None:
        return True
    current = to_jst(now) if now is not None else to_jst(datetime.now().astimezone())
    return (current - to_jst(fetched_at)) >= timedelta(days=days)


def _finish(
    day: date,
    state: DayState,
    periods: list[TimeRange],
    reasons: list[Reason],
    fetched_at: datetime | None,
    now: datetime | None,
    stale_after_days: int | None,
) -> DayVerdict:
    """鮮度の下限を当てて仕上げる。古ければ規則の根拠を残したまま unknown に落とす。"""
    if stale_after_days is not None and is_stale(fetched_at, now=now, days=stale_after_days):
        detail = (
            f"公式ページの取得が {stale_after_days} 日以上できていない"
            if fetched_at is not None
            else "公式ページを取得できていない"
        )
        reasons = [Reason(code=ReasonCode.stale_source, detail=detail), *reasons]
        return DayVerdict(day=day, state=DayState.unknown, periods=[], reasons=reasons)
    return DayVerdict(day=day, state=state, periods=periods, reasons=reasons)


def resolve_week(start: date, *, days: int = 7, **kwargs: object) -> list[DayVerdict]:
    """その日から days 日分の判定。ページの「7 日分の帯」に使う。"""
    return [resolve_day(start + timedelta(days=i), **kwargs) for i in range(days)]  # type: ignore[arg-type]
