"""巡回間隔の適応のテスト（ネットワーク不要）。

動きの無いサイトは間隔を延ばし、動いたら翌日から毎日に戻す。下限は週 1 回。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sitemill.diff.schedule import IntervalPolicy, source_due
from sitemill.diff.state import CrawlState, UrlState

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)
POLICY = IntervalPolicy()


def _state(
    *, fetched_days: float | None, changed_days: float | None, error: str | None = None
) -> CrawlState:
    st = CrawlState()
    st.urls["https://x.example/akiya/"] = UrlState(
        url="https://x.example/akiya/",
        source_id="s1",
        kind="listing_index",
        fetched_at=NOW - timedelta(days=fetched_days) if fetched_days is not None else None,
        changed_at=NOW - timedelta(days=changed_days) if changed_days is not None else None,
        error=error,
    )
    return st


def test_interval_grows_as_a_site_stays_still() -> None:
    assert POLICY.interval_days(None) == 1  # 変化を見たことがない
    assert POLICY.interval_days(0.5) == 1
    assert POLICY.interval_days(6.9) == 1  # 直近 7 日に変化 → 毎日
    assert POLICY.interval_days(7.0) == 3
    assert POLICY.interval_days(27.9) == 3
    assert POLICY.interval_days(28.0) == 7  # これ以上は週 1 回で止める
    assert POLICY.interval_days(400) == 7


def test_a_site_that_changed_recently_is_crawled_every_day() -> None:
    due, interval, why = source_due(
        "s1", _state(fetched_days=1, changed_days=2), now=NOW, policy=POLICY
    )
    assert due and interval == 1 and "変化が新しい" in why


def test_a_quiet_site_is_skipped_until_its_interval_passes() -> None:
    quiet = _state(fetched_days=1, changed_days=40)
    due, interval, why = source_due("s1", quiet, now=NOW, policy=POLICY)
    assert not due and interval == 7 and "前回取得から" in why
    # 7 日経てば必ず取りに行く（下限は週 1 回）
    due, interval, _ = source_due(
        "s1", _state(fetched_days=7, changed_days=40), now=NOW, policy=POLICY
    )
    assert due and interval == 7


def test_never_fetched_or_failing_sources_are_always_crawled() -> None:
    assert source_due("s1", CrawlState(), now=NOW, policy=POLICY)[0]
    assert source_due("s1", _state(fetched_days=None, changed_days=None), now=NOW, policy=POLICY)[0]
    failing = _state(fetched_days=0.5, changed_days=40, error="HTTP 500")
    due, _, why = source_due("s1", failing, now=NOW, policy=POLICY)
    assert due and why == "前回エラー"


def test_the_middle_band_is_every_three_days() -> None:
    mid = _state(fetched_days=1, changed_days=14)
    assert source_due("s1", mid, now=NOW, policy=POLICY) == (
        False,
        3,
        "前回取得から 1.0 日（間隔 3 日）",
    )
    due, _, why = source_due("s1", _state(fetched_days=3, changed_days=14), now=NOW, policy=POLICY)
    assert due and why == "3 日ごと"
