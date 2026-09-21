"""日次パイプラインの週次まとめ（実行時間・費用・heal・抽出の充足率）。外部アクセスはしない。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sitemill.metrics import weekly
from sitemill.settings import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import make_workspace  # noqa: E402


def _run(ws: Workspace, name: str, day: str, **payload: object) -> None:
    body = {
        "service": "dummy",
        "command": name,
        "ci": True,
        "started_at": f"{day}T18:00:00+00:00",
        "finished_at": f"{day}T18:05:00+00:00",
        "errors": [],
        "llm": {"calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0},
        "stages": {},
    }
    body.update(payload)
    ws.runs_dir.mkdir(parents=True, exist_ok=True)
    (ws.runs_dir / f"{day.replace('-', '')}-000000-{name}.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    make_workspace(tmp_path)
    return Workspace.open(tmp_path)


def _metrics(parsed: int, total: int) -> dict:
    return {"fields": {"price": {"parsed": parsed, "total": total, "not_found": total - parsed}}}


def test_the_table_adds_up_time_cost_and_heal(ws: Workspace) -> None:
    _run(
        ws,
        "crawl",
        "2026-09-10",
        stages={"crawl": {"fetched": 100, "changed": 20}, "heal": {"checked": 3, "downgraded": 1}},
    )
    _run(
        ws,
        "extract",
        "2026-09-11",
        stages={"extract": {"pages": 10, "items": 12}},
        llm={"calls": 10, "cached_calls": 0, "input_tokens": 1_000_000, "output_tokens": 200_000},
    )
    rows = weekly.collect(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC))
    assert [r.date for r in rows] == ["2026-09-10", "2026-09-11"]
    assert rows[0].fetched == 100 and rows[0].heal_checked == 3
    # claude-haiku-4-5 は入力 $1／出力 $5（100 万トークンあたり）
    assert rows[1].cost(weekly.price_of("claude-haiku-4-5")) == pytest.approx(2.0)

    text = "\n".join(weekly.report(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC)))
    # public リポジトリでは Actions の無料枠を消費しないので、枠との比較は出さない
    assert "1 か月の見込み" in text and "無料枠" not in text
    assert "自己修復（heal）: 点検 3 件" in text


def test_an_unknown_model_does_not_invent_a_price(ws: Workspace, tmp_path: Path) -> None:
    """単価を知らないモデルで費用を出すと嘘になる。出さずに、単価が無いことを書く。"""
    _run(ws, "extract", "2026-09-10", llm={"calls": 1, "input_tokens": 10, "output_tokens": 5})
    text = "\n".join(
        weekly.report(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC), model="mystery-model")
    )
    assert "単価が未登録" in text and "$" not in text.split("単価が未登録")[0].split("| 費用")[-1]


def test_the_extraction_rate_shows_its_movement(ws: Workspace) -> None:
    """抽出の劣化に気づくための数字。最初と最後を並べる。"""
    _run(ws, "extract", "2026-09-10", extraction_metrics=_metrics(8, 10))
    _run(ws, "extract", "2026-09-11", extraction_metrics=_metrics(4, 10))
    shifts = weekly.field_shift(weekly.collect(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC)))
    assert shifts == [("price", 0.8, 0.4)]
    text = "\n".join(weekly.report(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC)))
    assert "| price | 80% | 40% | -40pt |" in text


def test_local_runs_are_not_counted_as_the_daily_pipeline(ws: Workspace) -> None:
    """手元の作業を混ぜると、日次の所要時間と費用を読み違える。"""
    _run(ws, "crawl", "2026-09-10", ci=False, stages={"crawl": {"fetched": 999}})
    assert weekly.collect(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC)) == []
    rows = weekly.collect(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC), source="all")
    assert rows[0].fetched == 999


def test_a_week_with_no_runs_says_so(ws: Workspace) -> None:
    text = "\n".join(weekly.report(ws, days=7, now=datetime(2026, 9, 12, tzinfo=UTC)))
    assert "この期間に記録がありません" in text
