"""巡回の間隔を、そのサイトの変化の起きかたに合わせて決める。

毎日すべてを取りに行くと相手サイトへの負荷が積み上がる。動きの無いサイトは間隔を延ばし、
動いたサイトは翌日から毎日に戻す。下限は週 1 回で、それ以上空けることはない。

新規掲載に気づくまでの最大の遅れ＝そのサイトの間隔（毎日なら 1 日、週 1 なら 7 日）。
平均はその半分。変化を見つけた翌日から毎日に戻るので、動いているサイトは常に 1 日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sitemill.diff.state import CrawlState


@dataclass(frozen=True)
class IntervalPolicy:
    """間隔の決め方。settings の CrawlConfig から作る。"""

    fresh_days: int = 7
    slow_after_days: int = 28
    mid_interval_days: int = 3
    max_interval_days: int = 7

    def interval_days(self, days_since_change: float | None) -> int:
        """最後に変化してからの日数から、次に取りに行くまでの間隔を決める。"""
        if days_since_change is None:  # 変化を見たことがない（初回や取得失敗が続く）
            return 1
        if days_since_change < self.fresh_days:
            return 1
        if days_since_change < self.slow_after_days:
            return self.mid_interval_days
        return self.max_interval_days


def _days(now: datetime, then: datetime | None) -> float | None:
    if then is None:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    return (now - then).total_seconds() / 86400


def source_due(
    source_id: str,
    state: CrawlState,
    *,
    now: datetime,
    policy: IntervalPolicy,
) -> tuple[bool, int, str]:
    """この source を今日取りに行くか。(取りに行くか, 間隔, 理由) を返す。

    まだ取ったことがない・前回エラーだった source は必ず取りに行く。
    """
    rows = state.for_source(source_id)
    if not rows:
        return True, 1, "未取得"
    if any(r.error for r in rows):
        return True, 1, "前回エラー"
    fetched = [r.fetched_at for r in rows if r.fetched_at]
    if not fetched:
        return True, 1, "未取得"
    changed = [r.changed_at for r in rows if r.changed_at]
    since_change = _days(now, max(changed)) if changed else None
    interval = policy.interval_days(since_change)
    since_fetch = _days(now, max(fetched)) or 0.0
    if since_fetch >= interval - 0.25:  # 実行時刻の揺れで 1 日飛ばさないよう少し緩める
        why = "変化が新しい" if interval == 1 else f"{interval} 日ごと"
        return True, interval, why
    return False, interval, f"前回取得から {since_fetch:.1f} 日（間隔 {interval} 日）"
