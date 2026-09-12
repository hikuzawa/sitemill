"""crawl → extract → build を、respx のダミーサイトと fixture プロバイダで通す結合テスト。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest
import respx

from sitemill import commands
from sitemill.build.site import BuildError, SiteBuilder, yen
from sitemill.extract.llm import FixtureProvider
from sitemill.service import load_service, load_sources_yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dummy_service import DummyService, make_workspace  # noqa: E402

INDEX = (
    '<html><body><main><a href="/bukken/1">物件1</a>'
    '<a href="/bukken/2">物件2</a></main></body></html>'
)
DETAIL = "<html><body><main><h1>物件番号 {n}</h1><p>価格 {price}万円</p></main></body></html>"


@pytest.fixture
def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> commands.Runtime:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    make_workspace(tmp_path)
    return commands.Runtime.open(tmp_path, service=DummyService())


def _mock_site() -> None:
    respx.get("https://akiya.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://akiya.example/").mock(return_value=httpx.Response(200, text=INDEX))
    respx.get("https://akiya.example/bukken/1").mock(
        return_value=httpx.Response(200, text=DETAIL.format(n=1, price=300))
    )
    respx.get("https://akiya.example/bukken/2").mock(
        return_value=httpx.Response(200, text=DETAIL.format(n=2, price="1,200"))
    )


def _responses() -> list[dict]:
    return [
        {"listings": []},  # index ページ
        {"listings": [{"listing_no_quote": "1", "price_quote": "300万円", "title": "物件1"}]},
        {"listings": [{"listing_no_quote": "2", "price_quote": "1,200万円", "title": "物件2"}]},
    ]


@respx.mock
def test_crawl_extract_build_end_to_end(rt: commands.Runtime) -> None:
    _mock_site()
    crawl = commands.cmd_crawl(rt)
    assert crawl.stages["crawl"]["fetched"] == 3 and crawl.stages["crawl"]["changed"] == 3
    assert (rt.ws.runs_dir / "latest-crawl.json").is_file()

    extract = commands.cmd_extract(rt, provider=FixtureProvider(_responses()))
    assert extract.stages["extract"]["pages"] == 3
    assert extract.stages["ingest"]["created"] == 2
    assert extract.extraction_metrics is not None
    assert extract.extraction_metrics["fields"]["price"]["parsed"] == 2
    records = (rt.ws.records_dir / "dummy-city.jsonl").read_text(encoding="utf-8")
    assert '"listing_no": "2"' in records and '"value": 12000000' in records

    # 2 回目の extract は pending が無いので何もしない
    again = commands.cmd_extract(rt, provider=FixtureProvider([]))
    assert "extract" not in again.stages or again.stages["extract"].get("pages", 0) == 0

    build = commands.cmd_build(rt)
    assert build.stages["build"]["pages"] == 3  # index + about + 404
    dist = rt.ws.dist_dir
    html = (dist / "index.html").read_text(encoding="utf-8")
    assert "data-sitemill-trust" in html and "テスト運営" in html and "1,200万円" in html
    assert 'rel="canonical"' in html and 'property="og:title"' in html
    assert "application/ld+json" in html
    assert (dist / "404.html").is_file() and (dist / "about" / "index.html").is_file()
    assert (dist / "static" / "style.css").is_file()
    assert (dist / "search" / "index.json").read_text(encoding="utf-8").count('"no"') == 2
    assert "https://dummy.example/" in (dist / "sitemap.xml").read_text(encoding="utf-8")
    assert "Sitemap: https://dummy.example/sitemap.xml" in (dist / "robots.txt").read_text()
    assert "/go/test https://example.com/offer 302" in (dist / "_redirects").read_text()

    status = commands.cmd_status(rt)
    assert status["by_source"]["dummy-city"]["records"] == 2
    assert status["by_source"]["dummy-city"]["pending"] == 0

    plan = commands.cmd_deploy(rt, dry_run=True)
    assert plan.ok and plan.files >= 6


def _build_with_contact(tmp_path: Path, block: str) -> str:
    make_workspace(tmp_path)
    site = tmp_path / "site.toml"
    site.write_text(
        site.read_text(encoding="utf-8").replace('contact = "test@example.com"', block),
        encoding="utf-8",
    )
    rt = commands.Runtime.open(tmp_path, service=DummyService())
    SiteBuilder(rt.ws, rt.service).build()
    return (rt.ws.dist_dir / "index.html").read_text(encoding="utf-8")


def test_trust_block_links_a_contact_url(tmp_path: Path) -> None:
    block = """
contact = "https://forms.example/contact"
contact_label = "お問い合わせフォーム"
""".strip()
    html = _build_with_contact(tmp_path, block)
    assert (
        '連絡先: <a href="https://forms.example/contact" rel="noopener" '
        'target="_blank">お問い合わせフォーム</a>'
    ) in html


def test_trust_block_falls_back_to_the_url_when_no_label(tmp_path: Path) -> None:
    html = _build_with_contact(tmp_path, 'contact = "https://forms.example/contact"')
    assert ">https://forms.example/contact</a>" in html


def test_trust_block_keeps_a_non_url_contact_as_text(rt: commands.Runtime) -> None:
    SiteBuilder(rt.ws, rt.service).build()
    html = (rt.ws.dist_dir / "index.html").read_text(encoding="utf-8")
    assert "連絡先: test@example.com" in html and 'href="test@example.com"' not in html


def test_build_fails_without_trust_block(rt: commands.Runtime) -> None:
    (rt.ws.templates_dir / "broken.html").write_text(
        '<!doctype html><html lang="ja"><head><title>x</title></head><body>no trust</body></html>',
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="data-sitemill-trust"):
        SiteBuilder(rt.ws, rt.service).build()


def test_extract_without_api_key_stops_with_env_hint(rt: commands.Runtime) -> None:
    from sitemill.settings import SecretsError, Workspace

    (rt.ws.root / "site.toml").write_text(
        (rt.ws.root / "site.toml")
        .read_text(encoding="utf-8")
        .replace('provider = "fixture"', 'provider = "anthropic"'),
        encoding="utf-8",
    )
    rt2 = commands.Runtime(ws=Workspace.open(rt.ws.root), service=rt.service)
    with pytest.raises(SecretsError, match="ANTHROPIC_API_KEY"):
        commands.cmd_extract(rt2)


def test_yen_filter() -> None:
    assert yen(None) == "—"
    assert yen(68_000) == "6.8万円"
    assert yen(400_000) == "40万円"  # 万円区切りに統一（旧: 400,000円）
    assert yen(9_000) == "9,000円"  # 1 万円未満は円
    assert yen(10_000) == "1万円"
    assert yen(0) == "0円"
    assert yen(9_800_000) == "980万円"
    assert yen(12_000_000) == "1,200万円"
    assert yen(120_000_000) == "1億2,000万円"
    assert yen(9_805_000) == "980.5万円"
    assert yen(100_000_000) == "1億円"


def test_load_service_and_sources_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "svc_mod.py").write_text(
        "from tests.dummy_service import DummyService\nservice = DummyService()\n", encoding="utf-8"
    )
    importlib.invalidate_caches()
    assert load_service("svc_mod:service").id == "dummy"
    with pytest.raises(ValueError):
        load_service("no-colon")
    (tmp_path / "bad.py").write_text("service = object()\n", encoding="utf-8")
    # 書いた直後の import は、ディレクトリの一覧がキャッシュされていて見つからないことがある
    # （「No module named 'bad'」で時々落ちた）。作った直後にキャッシュを捨てる
    importlib.invalidate_caches()
    with pytest.raises(TypeError):
        load_service("bad:service")

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  - id: a\n    name: A市\n    operator: A市\n    official_url: https://a.example/\n"
        "    municipality_code: '20219'\n"
        "    policy: link_only\n",
        encoding="utf-8",
    )
    sources = load_sources_yaml(yaml_path)
    assert sources[0].id == "a" and not sources[0].crawlable


def test_eval_record_replaces_fixture_response(rt: commands.Runtime) -> None:
    case = rt.ws.fixtures_dir / "eval" / "case1"
    case.mkdir(parents=True)
    (case / "page.html").write_text(
        "<html><body><main><p>物件番号 7</p><p>価格 300万円</p></main></body></html>",
        encoding="utf-8",
    )
    (case / "meta.json").write_text(
        '{"url": "https://akiya.example/bukken/7", "kind": "listing_detail", '
        '"key_field": "listing_no"}',
        encoding="utf-8",
    )
    (case / "llm_response.json").write_text('{"listings": []}', encoding="utf-8")
    (case / "expected.json").write_text(
        '{"records": [{"listing_no": "7", "price": 3000000}]}', encoding="utf-8"
    )
    fresh = FixtureProvider([{"listings": [{"listing_no_quote": "7", "price_quote": "300万円"}]}])
    result = commands.cmd_eval(rt, record=True, provider=fresh)
    assert result.cases == 1 and result.accuracy["price"].correct == 1
    assert result.recorded[0]["case"] == "case1"
    assert '"price_quote": "300万円"' in (case / "llm_response.json").read_text(encoding="utf-8")
    assert (case / "llm_response.previous.json").read_text(
        encoding="utf-8"
    ).strip() == '{"listings": []}'


def test_report_records_whether_it_ran_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """日次（Actions）と手元の作業を混ぜずに数えるため、実行元を残す。"""
    from sitemill.metrics.reports import new_report

    monkeypatch.delenv("CI", raising=False)
    assert new_report("svc", "crawl").ci is False
    monkeypatch.setenv("CI", "true")
    assert new_report("svc", "crawl").ci is True
