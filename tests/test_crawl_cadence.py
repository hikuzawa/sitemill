"""ページ種別ごとの巡回間隔（ADR 0018）。

変化の少ない source は間隔を延ばすが、告知（お知らせ・運休）のページは毎日取りに行く。
臨時休業と運休は「変化の少ないページに突然出る」ので、間隔を延ばすと最も重要な情報を取り逃がす。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from sitemill import commands
from sitemill.diff.state import CrawlState, UrlState
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.crawler import crawl_source
from sitemill.models import CrawlPolicy, OperatorEvidence, OperatorKind, SeedPage, Source
from sitemill.store import RawCache

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import DummyService, make_workspace  # noqa: E402

HOURS_URL = "https://spot.example/hours/"
NOTICE_URL = "https://spot.example/news/"
PAGE = "<html><body><main><h1>{title}</h1><p>10:00〜17:00</p></main></body></html>"
NOW = datetime(2026, 9, 12, 21, 0, tzinfo=UTC)


def _source() -> Source:
    return Source(
        id="spot",
        name="例の美術館",
        operator="公益財団法人 例",
        operator_kind=OperatorKind.facility_official,
        operator_evidence=OperatorEvidence(quote="運営: 公益財団法人 例", url=HOURS_URL),
        policy=CrawlPolicy.crawl,
        official_url="https://spot.example/",
        pages=[
            SeedPage(url=HOURS_URL, kind="spot_hours"),
            SeedPage(url=NOTICE_URL, kind="notice"),
        ],
    )


def _client() -> PoliteClient:
    return PoliteClient("sitemill/test", jitter=0.0, sleep=lambda _s: None)


@pytest.fixture
def site() -> None:
    respx.get("https://spot.example/robots.txt").mock(return_value=httpx.Response(404))
    for url, title in ((HOURS_URL, "開館時間"), (NOTICE_URL, "お知らせ")):
        respx.get(url).mock(
            return_value=httpx.Response(
                200, text=PAGE.format(title=title), headers={"content-type": "text/html"}
            )
        )


@respx.mock
def test_only_kinds_restricts_the_seed_pages(site: None, tmp_path: Path) -> None:
    state, raw = CrawlState(), RawCache(tmp_path)
    with _client() as client:
        summary = crawl_source(_source(), client, state, raw, only_kinds=frozenset({"notice"}))
    assert [p.url for p in summary.pages] == [NOTICE_URL]


@respx.mock
def test_only_kinds_with_no_matching_seed_fetches_nothing(site: None, tmp_path: Path) -> None:
    state, raw = CrawlState(), RawCache(tmp_path)
    with _client() as client:
        summary = crawl_source(_source(), client, state, raw, only_kinds=frozenset({"timetable"}))
    assert summary.pages == []


class _SpotService(DummyService):
    """告知ページを持つ施設 1 件だけのサービス。"""

    id = "spot-service"
    crawlable_operator_kinds = (OperatorKind.facility_official,)

    def sources(self, ws: object) -> list[Source]:
        return [_source()]


def _workspace(tmp_path: Path, *, always_daily: str) -> commands.Runtime:
    make_workspace(tmp_path)
    site_toml = tmp_path / "site.toml"
    site_toml.write_text(
        site_toml.read_text(encoding="utf-8").replace(
            "jitter_seconds = 0", f"jitter_seconds = 0\n{always_daily}"
        ),
        encoding="utf-8",
    )
    return commands.Runtime.open(tmp_path, service=_SpotService())


def _seen_recently(rt: commands.Runtime) -> None:
    """両ページを「昨日取得して、変化は 60 日前」の状態にする（間隔は週 1 に延びる）。"""
    state = CrawlState()
    for url, kind in ((HOURS_URL, "spot_hours"), (NOTICE_URL, "notice")):
        state.urls[url] = UrlState(
            url=url,
            source_id="spot",
            kind=kind,
            fetched_at=NOW - timedelta(days=1),
            changed_at=NOW - timedelta(days=60),
            content_hash="x",
        )
    state.save(rt.state_path)


@respx.mock
def test_notice_pages_are_fetched_even_when_the_source_is_not_due(
    site: None, tmp_path: Path
) -> None:
    rt = _workspace(tmp_path, always_daily='always_daily_kinds = ["notice"]')
    _seen_recently(rt)
    report = commands.cmd_crawl(rt)
    assert report.stages["crawl"].get("daily_kinds_only") == 1
    assert report.stages["crawl"].get("skipped_not_due") is None
    fetched = [call.request.url for call in respx.calls if "robots" not in str(call.request.url)]
    assert [str(u) for u in fetched] == [NOTICE_URL]  # 開館時間のページは取りに行かない


@respx.mock
def test_without_the_setting_the_whole_source_is_skipped(site: None, tmp_path: Path) -> None:
    rt = _workspace(tmp_path, always_daily="")
    _seen_recently(rt)
    report = commands.cmd_crawl(rt)
    assert report.stages["crawl"].get("skipped_not_due") == 1
    assert report.stages["crawl"].get("daily_kinds_only") is None
    assert [c.request.url for c in respx.calls] == []


@respx.mock
def test_a_due_source_still_fetches_every_page(site: None, tmp_path: Path) -> None:
    rt = _workspace(tmp_path, always_daily='always_daily_kinds = ["notice"]')
    report = commands.cmd_crawl(rt)  # 巡回状態が無い＝初回なので全ページ取る
    assert report.stages["crawl"].get("daily_kinds_only") is None
    fetched = {
        str(call.request.url) for call in respx.calls if "robots" not in str(call.request.url)
    }
    assert fetched == {HOURS_URL, NOTICE_URL}


@respx.mock
def test_a_new_seed_is_fetched_even_when_the_source_is_not_due(site: None, tmp_path: Path) -> None:
    """seed を足した直後に「期日でない」で見送ると、最長 7 日間その情報が欠ける。"""
    rt = _workspace(tmp_path, always_daily="")
    state = CrawlState()
    # 開館時間のページだけ「昨日取得・変化は 60 日前」にして、告知ページは未取得のままにする
    state.urls[HOURS_URL] = UrlState(
        url=HOURS_URL,
        source_id="spot",
        kind="spot_hours",
        fetched_at=NOW - timedelta(days=1),
        changed_at=NOW - timedelta(days=60),
        content_hash="x",
    )
    state.save(rt.state_path)
    report = commands.cmd_crawl(rt)
    assert report.stages["crawl"].get("new_seeds") == 1
    fetched = {
        str(call.request.url) for call in respx.calls if "robots" not in str(call.request.url)
    }
    assert NOTICE_URL in fetched


def test_extract_forgets_urls_that_are_no_longer_seeded(tmp_path: Path) -> None:
    """seed から外した URL の状態は捨てる。残ると、外したページを取り込み続ける。

    japan-open-today で、屋島に別の施設（温泉）の URL を seed していたのを直したのに、
    状態が残っていたため温泉の事実が入り続けた。宣言したサービスだけこの掃除をする。
    """
    from sitemill.diff.state import CrawlState, UrlState

    state = CrawlState(
        urls={
            "https://example.jp/a": UrlState(url="https://example.jp/a", source_id="s1"),
            "https://example.jp/gone": UrlState(url="https://example.jp/gone", source_id="s1"),
            "https://other.jp/x": UrlState(url="https://other.jp/x", source_id="s2"),
        }
    )
    assert state.forget(["https://example.jp/gone", "https://example.jp/never"]) == 1
    assert sorted(state.urls) == ["https://example.jp/a", "https://other.jp/x"]
    del tmp_path
