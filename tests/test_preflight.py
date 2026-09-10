"""公開前チェック（ページ個別・サイト全体）のテスト。"""

from __future__ import annotations

from pathlib import Path

from sitemill.build import preflight

GOOD_HEAD = (
    '<link rel="canonical" href="https://x/">'
    '<meta property="og:title" content="t">'
    '<meta property="og:type" content="website">'
    '<meta property="og:url" content="https://x/">'
    '<script type="application/ld+json">{"@type":"WebSite"}</script>'
)


def _page(head: str = GOOD_HEAD, body: str = "<h1>x</h1>") -> str:
    return f'<!doctype html><html lang="ja"><head>{head}</head><body>{body}</body></html>'


def test_good_page_passes() -> None:
    assert preflight.check_page_html(_page(), path="index.html") == []


def test_missing_canonical_ogp_jsonld_flagged() -> None:
    problems = preflight.check_page_html(_page(head=""), path="p.html")
    joined = " ".join(problems)
    assert "canonical" in joined and "OGP" in joined and "JSON-LD" in joined


def test_invalid_jsonld_flagged() -> None:
    head = GOOD_HEAD.replace('{"@type":"WebSite"}', "{oops,}")
    problems = preflight.check_page_html(_page(head=head), path="p.html")
    assert any("不正な JSON" in p for p in problems)


def test_noindex_exempt_from_canonical_ogp_jsonld() -> None:
    assert preflight.check_page_html(_page(head=""), path="404.html", noindex=True) == []


def test_visible_text_and_contacts_extracts_mailto_tel() -> None:
    html = _page(body='<a href="mailto:a@b.com">mail</a><a href="tel:0312345678">tel</a>本文')
    out = preflight.visible_text_and_contacts(html)
    assert "a@b.com" in out and "0312345678" in out and "本文" in out


def _write_site(dist: Path, *, beacon: bool = False, about: bool = True) -> None:
    enc = {"encoding": "utf-8"}
    (dist / "robots.txt").write_text("User-agent: *\nSitemap: https://x/sitemap.xml\n", **enc)
    (dist / "sitemap.xml").write_text("<urlset/>", **enc)
    (dist / "404.html").write_text(_page(body="404"), **enc)
    beacon_tag = f'<script src="{preflight.ANALYTICS_BEACON}"></script>' if beacon else ""
    (dist / "index.html").write_text(_page(body=f"top{beacon_tag}"), **enc)
    if about:
        (dist / "about").mkdir(exist_ok=True)
        (dist / "about" / "index.html").write_text(_page(body="運営者 と 免責"), **enc)


def test_check_site_passes_when_complete(tmp_path: Path) -> None:
    _write_site(tmp_path)
    assert preflight.check_site(tmp_path, analytics_token=None) == []


def test_check_site_flags_missing_about(tmp_path: Path) -> None:
    _write_site(tmp_path, about=False)
    problems = preflight.check_site(tmp_path, analytics_token=None)
    assert any("/about" in p or "運営者情報" in p for p in problems)


def test_analytics_gating(tmp_path: Path) -> None:
    # トークン未設定なのに beacon が出ていれば失敗
    _write_site(tmp_path, beacon=True)
    problems = preflight.check_site(tmp_path, analytics_token=None)
    assert any("beacon" in p or "解析" in p for p in problems)
    # トークン設定済みで beacon があれば合格
    assert preflight.check_site(tmp_path, analytics_token="tok") == []


def test_analytics_missing_when_token_set(tmp_path: Path) -> None:
    _write_site(tmp_path, beacon=False)
    problems = preflight.check_site(tmp_path, analytics_token="tok")
    assert any("解析" in p for p in problems)
