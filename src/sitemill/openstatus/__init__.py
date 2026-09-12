"""「その日、開いているか」の判定（ADR 0018）。

状態は open / closed / unknown の 3 値だけで、必ず根拠（ReasonCode ＋ 原文の引用 ＋ 一次情報 URL
＋ 取得日時）を返す。表示の文言はサービスがロケールごとに用意する。
"""

from sitemill.openstatus.models import (
    UNCERTAIN_CODES,
    DayState,
    DayVerdict,
    Reason,
    ReasonCode,
)
from sitemill.openstatus.resolve import (
    evaluate_closures,
    is_stale,
    next_business_day,
    nth_of_weekday,
    periods_for,
    resolve_day,
    resolve_week,
)

__all__ = [
    "UNCERTAIN_CODES",
    "DayState",
    "DayVerdict",
    "Reason",
    "ReasonCode",
    "evaluate_closures",
    "is_stale",
    "next_business_day",
    "nth_of_weekday",
    "periods_for",
    "resolve_day",
    "resolve_week",
]
