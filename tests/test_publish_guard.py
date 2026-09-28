"""公開前の歯止め（ADR 0026）。外部アクセスはしない。"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sitemill.build.guard import (
    GuardLimit,
    GuardMetric,
    evaluate,
    load_baseline,
    run_guard,
)
from sitemill.settings import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import make_workspace  # noqa: E402

NOW = datetime(2026, 9, 28, 3, tzinfo=UTC)
LIMITS = {
    "unknown": GuardLimit(reason="平常の日の増減は最大 +3pt", max_rise=0.10, max_share=0.25),
}


class Svc:
    publish_limits = LIMITS

    def __init__(self, count: int, total: int = 281) -> None:
        self.metric = GuardMetric(count, total)

    def publish_metrics(self, ws: Workspace, *, now: datetime) -> dict[str, GuardMetric]:
        return {"unknown": self.metric}


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    make_workspace(tmp_path)
    return Workspace.open(tmp_path)


def test_a_normal_day_passes_and_becomes_the_baseline(ws: Workspace) -> None:
    result = run_guard(ws, Svc(20), now=NOW)
    assert result.ok and result.accepted
    assert load_baseline(ws)["unknown"] == {"count": 20, "total": 281, "share": 0.071174}


def test_a_jump_like_9_27_is_stopped_and_the_baseline_is_kept(ws: Workspace) -> None:
    """9/27 は不明が 1 日で +25pt（障害）。前日の本番を残し、基準も動かさない。"""
    run_guard(ws, Svc(20), now=NOW)
    result = run_guard(ws, Svc(89), now=NOW)
    assert not result.ok and not result.accepted
    assert any("+24.6pt" in b for b in result.breaches), result.breaches
    assert load_baseline(ws)["unknown"]["count"] == 20
    # 翌日もさらに悪ければ、最後に公開した 20 件と比べ続ける
    assert not run_guard(ws, Svc(95), now=NOW).ok


def test_a_slow_creep_is_caught_by_the_ceiling() -> None:
    """1 日ずつは小さくても、上限（25%）を越えたら止める。"""
    baseline = {"unknown": {"count": 65, "total": 281, "share": 65 / 281}}
    breaches = evaluate({"unknown": GuardMetric(73, 281)}, baseline, LIMITS)
    assert breaches and "上限 25%" in breaches[0]


def test_a_person_can_accept_a_real_change(ws: Workspace) -> None:
    run_guard(ws, Svc(20), now=NOW)
    result = run_guard(ws, Svc(89), now=NOW, accept=True)
    assert result.breaches and result.accepted
    assert load_baseline(ws)["unknown"]["count"] == 89


def test_shares_not_counts_are_compared() -> None:
    """施設が増えた日に件数だけで比べると、増えた分が悪化に見える。"""
    baseline = {"unknown": {"count": 20, "total": 281, "share": 20 / 281}}
    assert evaluate({"unknown": GuardMetric(40, 562)}, baseline, LIMITS) == []


def test_a_service_without_the_hook_is_not_checked(ws: Workspace) -> None:
    assert run_guard(ws, object(), now=NOW).ok


def test_a_limit_needs_a_reason() -> None:
    with pytest.raises(ValueError):
        GuardLimit(reason=" ", max_rise=0.1)
