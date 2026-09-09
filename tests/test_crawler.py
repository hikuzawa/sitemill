from pathlib import Path

import httpx
import pytest
import respx

from sitemill.diff.state import CrawlState
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.crawler import crawl_source
from sitemill.fetch.discover import discover_source
from sitemill.models import (
    CrawlPolicy,
    FollowRule,
    OperatorEvidence,
    OperatorKind,
    PageKind,
    SeedPage,
    Source,
)
from sitemill.store import RawCache

INDEX = """<html><body><main><h1>空き家バンク物件一覧</h1>
<ul><li><a href="/bukken/1">物件1</a></li><li><a href="/bukken/2">物件2</a></li>
<li><a href="https://other.example/bukken/9">外部</a></li>
<li><a href="/about">制度について</a></li></ul>
</main></body></html>"""
DETAIL = "<html><body><main><h1>物件{n}</h1><p>価格 {price}万円</p></main></body></html>"


def _source(max_pages: int = 30) -> Source:
    return Source(
        id="akiya-test",
        name="テスト市",
        operator="テスト市",
        operator_kind=OperatorKind.municipality,
        operator_evidence=OperatorEvidence(quote="運営: テスト市", url="https://akiya.example/"),
        policy=CrawlPolicy.crawl,
        official_url="https://akiya.example/",
        pages=[
            SeedPage(
                url="https://akiya.example/",
                kind=PageKind.listing_index,
                follow=[FollowRule(pattern=r"/bukken/\d+", kind=PageKind.listing_detail)],
            )
        ],
        max_pages=max_pages,
    )


def _client() -> PoliteClient:
    return PoliteClient(
        "sitemill/0.1 (t; +https://t.example/about/)", jitter=0.0, sleep=lambda s: None
    )


@pytest.fixture
def site() -> dict[str, respx.Route]:
    respx.get("https://akiya.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://other.example/robots.txt").mock(return_value=httpx.Response(404))
    state = {"price2": 500}

    def index(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == '"i1"':
            return httpx.Response(304)
        return httpx.Response(
            200, text=INDEX, headers={"etag": '"i1"', "content-type": "text/html; charset=utf-8"}
        )

    def detail2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=DETAIL.format(n=2, price=state["price2"]))

    routes = {
        "index": respx.get("https://akiya.example/").mock(side_effect=index),
        "d1": respx.get("https://akiya.example/bukken/1").mock(
            return_value=httpx.Response(200, text=DETAIL.format(n=1, price=300))
        ),
        "d2": respx.get("https://akiya.example/bukken/2").mock(side_effect=detail2),
        "other": respx.get("https://other.example/bukken/9").mock(
            return_value=httpx.Response(200, text="x")
        ),
        "about": respx.get("https://akiya.example/about").mock(
            return_value=httpx.Response(200, text="x")
        ),
    }
    routes["_state"] = state  # type: ignore[assignment]
    return routes


@respx.mock
def test_crawl_detects_changes_only_when_content_changes(tmp_path: Path, site: dict) -> None:
    source, state, raw = _source(), CrawlState(), RawCache(tmp_path / "raw")
    with _client() as client:
        first = crawl_source(source, client, state, raw)
    assert first.count("fetched") == 3 and first.count("changed") == 3
    assert {p.kind for p in first.pages} == {PageKind.listing_index, PageKind.listing_detail}
    assert site["other"].call_count == 0 and site["about"].call_count == 0
    assert all(s.pending_extract for s in state.for_source("akiya-test"))
    assert raw.load("akiya-test", "https://akiya.example/bukken/1") is not None

    # 抽出済みにしてから再巡回: index は 304、詳細は同内容 → 変化なし
    for s in state.for_source("akiya-test"):
        s.pending_extract = False
    with _client() as client:
        second = crawl_source(source, client, state, raw)
    assert second.count("not_modified") == 1 and second.count("changed") == 0
    assert second.count("unchanged") == 3
    assert not any(s.pending_extract for s in state.for_source("akiya-test"))

    # 物件2 の価格が変わる → その URL だけ pending になる
    site["_state"]["price2"] = 450
    with _client() as client:
        third = crawl_source(source, client, state, raw)
    changed = [p.url for p in third.pages if p.changed]
    assert changed == ["https://akiya.example/bukken/2"]
    pending = [s.url for s in state.pending("akiya-test")]
    assert pending == ["https://akiya.example/bukken/2"]
    assert state.get("https://akiya.example/bukken/2").changed_at is not None  # type: ignore[union-attr]


@respx.mock
def test_max_pages_limits_fetches(tmp_path: Path, site: dict) -> None:
    with _client() as client:
        summary = crawl_source(_source(max_pages=2), client, CrawlState(), RawCache(tmp_path))
    assert len(summary.pages) == 2
    assert site["index"].call_count == 1 and site["d1"].call_count + site["d2"].call_count == 1


@respx.mock
def test_link_only_source_makes_no_requests(tmp_path: Path, site: dict) -> None:
    source = Source(
        id="third",
        name="民間",
        operator="民間",
        operator_kind=OperatorKind.third_party,
        policy=CrawlPolicy.link_only,
        official_url="https://akiya.example/",
    )
    with _client() as client:
        summary = crawl_source(source, client, CrawlState(), RawCache(tmp_path))
    assert summary.pages == [] and site["index"].call_count == 0


@respx.mock
def test_discover_finds_candidates_from_links_and_sitemap(tmp_path: Path) -> None:
    respx.get("https://city.example/robots.txt").mock(
        return_value=httpx.Response(
            200, text="User-agent: *\nSitemap: https://city.example/sitemap.xml\n"
        )
    )
    respx.get("https://city.example/").mock(
        return_value=httpx.Response(
            200,
            text='<a href="/kurashi/akiyabank/">空き家バンク</a><a href="/kanko/">観光情報</a>'
            '<a href="/sumai/">住まい</a>',
        )
    )
    respx.get("https://city.example/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text="<urlset><url><loc>https://city.example/kurashi/akiyabank/bukken.html</loc></url>"
            "<url><loc>https://city.example/kanko/</loc></url></urlset>",
        )
    )
    source = Source(
        id="city",
        name="市",
        operator="市",
        operator_kind=OperatorKind.municipality,
        operator_evidence=OperatorEvidence(quote="市", url="https://city.example/"),
        policy=CrawlPolicy.crawl,
        official_url="https://city.example/",
        pages=[SeedPage(url="https://city.example/")],
    )
    with _client() as client:
        candidates = discover_source(source, client)
    urls = [c.url for c in candidates]
    assert urls[0] == "https://city.example/kurashi/akiyabank/"
    assert "https://city.example/kurashi/akiyabank/bukken.html" in urls
    assert "https://city.example/kanko/" not in urls
    assert candidates[0].score == 4
