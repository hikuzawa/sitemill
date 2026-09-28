"""運営主体の根拠を定期的に確かめ直す回し方（ADR 0027）。外部アクセスはしない。"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from sitemill.clock import JST
from sitemill.recheck import RecheckTarget, load_state, pick, recheck_lines, rotate
from sitemill.settings import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import make_workspace  # noqa: E402

DAY = date(2026, 9, 28)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    make_workspace(tmp_path)
    return Workspace.open(tmp_path)


def targets() -> list[RecheckTarget]:
    return [
        RecheckTarget("a", "A 市", date(2026, 9, 1)),
        RecheckTarget("b", "B 町", date(2026, 8, 1)),
        RecheckTarget("c", "C 村", None),
        RecheckTarget("d", "D 島", date(2026, 9, 20)),
    ]


def answers(**by_key: str):
    def check(target: RecheckTarget) -> tuple[str, str]:
        result = by_key.get(target.key, "ok")
        return result, "" if result == "ok" else f"{result} の理由"

    return check


def test_the_oldest_are_picked_first() -> None:
    chosen = pick(targets(), {}, per_night=2, wait_days=7, today=DAY)
    assert [t.key for t in chosen] == ["c", "b"]  # 確認日の無いもの、いちばん古いもの


def test_ok_moves_only_what_was_checked(ws: Workspace) -> None:
    """まとめて確認日を今日にしない。確かめた行だけ進める。

    akiya-atlas では 1,700 件以上が、確かめていないのに進んでいた。
    """
    rotate(ws, targets(), answers(), per_night=2, today=DAY)
    state = load_state(ws)
    assert state == {"b": {"checked_on": "2026-09-28"}, "c": {"checked_on": "2026-09-28"}}
    # 次の晩は残りの古い順
    chosen = pick(targets(), state, per_night=2, wait_days=7, today=date(2026, 9, 29))
    assert [t.key for t in chosen] == ["a", "d"]


def test_a_failure_is_retried_outside_the_quota_and_reselected_on_the_third_night(
    ws: Workspace,
) -> None:
    seen: list[str] = []

    def reselect(target: RecheckTarget, reason: str) -> dict:
        seen.append(target.key)
        return {"old": "https://old.example/", "new": "https://new.example/"}

    for i, day in enumerate((DAY, date(2026, 9, 29), date(2026, 9, 30))):
        report = rotate(ws, targets(), answers(c="fail"), per_night=1, today=day, reselect=reselect)
        if i < 2:
            assert load_state(ws)["c"]["failures"] == i + 1
    assert seen == ["c"]
    assert report.reselected == 1
    assert load_state(ws)["c"] == {"checked_on": "2026-09-30"}
    # 再試行は枠の外。3 晩の間も古い順の枠（1 件）は進んでいる
    assert {"b", "a"} <= set(load_state(ws))


def test_without_a_reselect_hook_a_failure_keeps_being_reported(ws: Workspace) -> None:
    for day in (DAY, date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)):
        rotate(ws, targets(), answers(c="fail"), per_night=1, today=day)
    st = load_state(ws)["c"]
    assert st["failures"] == 4 and st["failed_since"] == "2026-09-28"


def test_unreachable_and_robots_are_not_failures_and_wait_a_week(ws: Workspace) -> None:
    """海外の実行環境からの接続を落とす国内サイトで、選び直しが空回りしないように。"""
    rotate(ws, targets(), answers(c="unreachable", b="skip"), per_night=2, today=DAY)
    state = load_state(ws)
    assert "failures" not in state["c"] and "checked_on" not in state["c"]
    assert state["c"]["waiting_since"] == "2026-09-28"
    assert state["b"]["waiting_reason"] == "skip の理由"
    chosen = pick(targets(), state, per_night=4, wait_days=7, today=date(2026, 10, 4))
    assert {t.key for t in chosen} == {"a", "d"}  # 7 日たつまで待たせる
    chosen = pick(targets(), state, per_night=4, wait_days=7, today=date(2026, 10, 5))
    assert {"b", "c"} <= {t.key for t in chosen}


def test_a_crash_in_one_check_does_not_stop_the_night(ws: Workspace) -> None:
    def check(target: RecheckTarget) -> tuple[str, str]:
        if target.key == "c":
            raise RuntimeError("boom")
        return "ok", ""

    report = rotate(ws, targets(), check, per_night=2, today=DAY)
    assert report.counts["fail"] == 1 and report.counts["ok"] == 1


def test_the_weekly_lines(ws: Workspace) -> None:
    rotate(ws, targets(), answers(c="fail", b="unreachable"), per_night=3, today=DAY)
    now = datetime(2026, 9, 28, 23, 0, tzinfo=JST)
    lines = recheck_lines(ws, targets(), days=7, now=now)
    text = "\n".join(lines)
    assert "成り立った 1・成り立たなかった 1・robots.txt で見送り 0・通信できない 1" in text
    # 通信できなかった B 町は確認日が動かない（8/1 のまま）。C 村は確認日が無い
    assert "いちばん古い確認日 2026-08-01（確認日の無いもの 1 件）" in text
    assert "成り立たないもの: 1 件" in text and "C 村（2026-09-28 から 1 晩）" in text
    assert "確かめられていないもの: 1 件" in text and "B 町（2026-09-28 から）" in text
    log = (ws.runs_dir / "operator-rechecks.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["result"] for line in log} == {"ok", "fail", "unreachable"}
