"""サイトマップの lastmod を「中身が最後に変わった日」にする（ADR 0025）。外部アクセスはしない。"""

from __future__ import annotations

import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sitemill import commands
from sitemill.build.lastmod import LastmodLedger, fingerprint
from sitemill.build.site import SiteBuilder

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import DummyService, make_workspace  # noqa: E402

PAGE = """<html><body><header>ナビ</header><main>
<h1>屋島寺</h1>
<p class="verdict" data-sitemill-volatile>{day}は開館</p>
<ul>{items}</ul>
</main><footer><section data-sitemill-trust>最終更新 {day}</section></footer></body></html>"""


def _page(day: str = "9月17日", items: str = "<li>a</li>\n<li>b</li>") -> str:
    return PAGE.format(day=day, items=items)


def test_the_date_drawn_for_the_day_is_not_a_change() -> None:
    """「9 月 17 日は開館」や信頼シグナルの最終更新は、事実ではなくその日に描いた結果。"""
    assert fingerprint(_page("9月17日")) == fingerprint(_page("9月18日"))


def test_reordering_a_list_is_not_a_change() -> None:
    """今日の状態で並びが変わる一覧に、印を付けなくても済むようにする。"""
    assert fingerprint(_page(items="<li>a</li>\n<li>b</li>")) == fingerprint(
        _page(items="<li>b</li>\n<li>a</li>")
    )


def test_a_real_change_in_main_is_a_change() -> None:
    assert fingerprint(_page()) != fingerprint(_page(items="<li>a</li>\n<li>c</li>"))


def test_the_ledger_keeps_the_day_the_content_last_changed(tmp_path: Path) -> None:
    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    first = ledger.update("a.html", _page(), today=date(2026, 9, 17), first_seen=date(2026, 9, 10))
    assert first == date(2026, 9, 10)  # 初めて見たページは、サービスが持つ事実の日付
    ledger.save()

    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    same = ledger.update(
        "a.html", _page("9月18日"), today=date(2026, 9, 18), first_seen=date(2026, 9, 18)
    )
    assert same == date(2026, 9, 10) and ledger.kept == 1
    ledger.save()

    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    moved = ledger.update(
        "a.html", _page(items="<li>c</li>"), today=date(2026, 9, 19), first_seen=date(2026, 9, 19)
    )
    assert moved == date(2026, 9, 19) and ledger.changed == 1


def test_a_first_seen_date_never_runs_ahead_of_today(tmp_path: Path) -> None:
    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    day = ledger.update("a.html", _page(), today=date(2026, 9, 17), first_seen=date(2026, 9, 20))
    assert day == date(2026, 9, 17)


def test_pages_that_are_gone_are_not_carried_over(tmp_path: Path) -> None:
    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    ledger.update("a.html", _page(), today=date(2026, 9, 17), first_seen=date(2026, 9, 17))
    ledger.update("b.html", _page(), today=date(2026, 9, 17), first_seen=date(2026, 9, 17))
    ledger.save()
    ledger = LastmodLedger.load(tmp_path / "lastmod.json")
    ledger.update("a.html", _page(), today=date(2026, 9, 18), first_seen=date(2026, 9, 18))
    ledger.save()
    assert set(LastmodLedger.load(tmp_path / "lastmod.json").pages) == {"a.html"}


@pytest.fixture
def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> commands.Runtime:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    make_workspace(tmp_path)
    return commands.Runtime.open(tmp_path, service=DummyService())


def _lastmods(rt: commands.Runtime) -> set[str]:
    sitemap = (rt.ws.dist_dir / "sitemap.xml").read_text(encoding="utf-8")
    return set(re.findall(r"<lastmod>([^<]+)</lastmod>", sitemap))


def test_the_sitemap_keeps_yesterdays_date_when_nothing_changed(rt: commands.Runtime) -> None:
    """ダミーのサービスは信頼シグナルの最終更新に毎回「今」を入れる。

    それでも中身が同じなら lastmod は動かない。
    """
    day1 = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)  # JST 9/17 07:00
    day2 = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)  # JST 9/18 07:00
    first = SiteBuilder(rt.ws, rt.service, now=day1, track_lastmod=True).build()
    assert _lastmods(rt) == {"2026-09-17"} and first.lastmod_added == first.pages - 1  # 404 は除く
    second = SiteBuilder(rt.ws, rt.service, now=day2, track_lastmod=True).build()
    assert _lastmods(rt) == {"2026-09-17"}
    assert second.lastmod_kept == first.lastmod_added and second.lastmod_changed == 0


def test_a_builder_that_does_not_track_leaves_no_state(rt: commands.Runtime) -> None:
    """テストや調査で SiteBuilder を直接使っても、サービスの data/state を書き換えない。"""
    SiteBuilder(rt.ws, rt.service, now=datetime(2026, 9, 17, tzinfo=UTC)).build()
    assert not (rt.ws.state_dir / "lastmod.json").exists()
    assert _lastmods(rt) == {"2026-09-17"}  # 記録が無ければ、これまでどおり信頼シグナルの日付


def test_sitemill_build_records_lastmod_and_reports_it(rt: commands.Runtime) -> None:
    report = commands.cmd_build(rt)
    assert (rt.ws.state_dir / "lastmod.json").is_file()
    stages = report.stages["build"]
    assert stages["lastmod_added"] >= 1 and stages["lastmod_changed"] == 0
