"""多言語サイトのビルド（ADR 0016）。hreflang・ロケール別の書式・文言カタログ・相互参照の検査。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sitemill.build.site import BuildError, SiteBuilder
from sitemill.models import (
    OperatorInfo,
    Page,
    PageMeta,
    Redirect,
    Source,
    SourceLink,
    TrustSignals,
)
from sitemill.settings import Workspace

SITE_TOML = """
[site]
id = "poly"
name = "Poly"
base_url = "https://poly.example"
service = "tests.test_build_i18n:service"
language = "ja"

[operator]
name = "テスト運営"
contact = "https://forms.example/contact"
contact_label = "問い合わせ先"

[[locales]]
code = "ja"
path = ""
label = "日本語"
default = true

[[locales]]
code = "en"
path = "en"
label = "English"

[[locales]]
code = "zh-Hant"
path = "zh-hant"
label = "繁體中文"
"""

BASE_TEMPLATE = """{% import "sitemill/macros.html" as sm %}<!doctype html>
<html lang="{{ locale.lang }}"><head><meta charset="utf-8">
<title>{{ meta.title }} | {{ site.name }}</title>
{{ sm.head_meta(meta, site, base_url, analytics=analytics, site_verification=site_verification,
                locale=locale, hreflangs=hreflangs) }}
</head>
<body>
<nav>
{% for lc in locales if lc.code in meta.alternates %}
<a href="{{ meta.alternates[lc.code] }}" hreflang="{{ lc.lang }}">{{ lc.display }}</a>
{% endfor %}
</nav>
<main>{% block content %}{% endblock %}</main>
{{ sm.trust_block_l(trust, locale) }}
</body></html>
"""

INDEX_TEMPLATE = """{% extends "base.html" %}
{% block content %}<h1>{{ t('home.title') }}</h1>
<p class="fee">{{ 2100|money_l }}</p>
<p class="day">{{ today|date_short_l }}</p>
<p class="note">{{ t('home.note') }}</p>
{% endblock %}
"""

ABOUT_TEMPLATE = """{% extends "base.html" %}
{% block content %}<h1>{{ t('about.title') }}</h1>
<p>{{ t('about.operator') }}: {{ site.operator.name }}</p>
<h2>{{ t('about.disclaimer_title') }}</h2><p>{{ t('about.disclaimer') }}</p>
{% endblock %}
"""

NOTFOUND_TEMPLATE = """{% extends "base.html" %}
{% block content %}<h1>404</h1>{% endblock %}
"""

CATALOGS = {
    "ja.yaml": """
home:
  title: 今日行ける日本
  note: 情報は公式サイトから毎日取得しています。
about:
  title: 運営者情報
  operator: 運営者
  disclaimer_title: 免責
  disclaimer: 内容の正確性は保証しません。
trust:
  title: この情報について
  updated: 最終更新
  timezone: （日本時間）
  count: データ件数
  count_value: "{count} 件"
  sources: 一次情報
  sources_default: 各施設の公式ページ
  fetched: "（取得: {when}）"
  operator: 運営者
  contact: 連絡先
  contact_link: お問い合わせフォーム
  details: 詳細
""",
    "en.yaml": """
home:
  title: Japan Open Today
  note: Fetched from official sites every day.
about:
  title: About
  operator: Operator
  disclaimer_title: Disclaimer
  disclaimer: We do not guarantee accuracy.
trust:
  title: About this information
  updated: Last updated
  timezone: (JST)
  count: Records
  count_value: "{count} records"
  sources: Primary sources
  sources_default: Official pages of each facility
  fetched: "(fetched {when})"
  operator: Operator
  contact: Contact
  contact_link: Contact form
  details: details
""",
    # 繁体字はわざと trust.* だけにして、未翻訳が既定ロケールに落ちることを確かめる
    "zh-Hant.yaml": """
trust:
  title: 關於這些資訊
  updated: 最後更新
  timezone: （日本時間）
  count: 資料筆數
  count_value: "{count} 筆"
  sources: 一手資訊
  sources_default: 各設施的官方網頁
  fetched: "（擷取: {when}）"
  operator: 營運者
  contact: 聯絡方式
  details: 詳細
""",
}

LOCALES = [("ja", ""), ("en", "en"), ("zh-Hant", "zh-hant")]


def _alternates(rel: str) -> dict[str, str]:
    """1 つの論理ページについて、全言語版の url_path を作る。"""
    out = {}
    for code, prefix in LOCALES:
        out[code] = f"/{prefix}/{rel}".replace("//", "/") if prefix else f"/{rel}"
    return out


class PolyService:
    """3 言語のトップと運営者情報だけを持つサービス。"""

    id = "poly"

    def __init__(self) -> None:
        self.break_alternates = False

    def sources(self, ws: Workspace) -> list[Source]:
        return []

    def extraction_spec(self, kind: str) -> None:
        return None

    def ingest(self, ws: Workspace, **kwargs: Any) -> dict[str, int]:
        return {}

    def finalize(self, ws: Workspace, *, now: datetime) -> None:
        return None

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        jsonld = [{"@context": "https://schema.org", "@type": "WebSite", "name": "Poly"}]
        pages: list[Page] = []
        trust = TrustSignals(
            updated_at=now,
            sources=[SourceLink(label="公式", url="https://example.com/", fetched_at=now)],
            operator=OperatorInfo(
                name=ws.site.operator.name,
                contact=ws.site.operator.contact,
                contact_label=ws.site.operator.contact_label,
            ),
            record_count=1234,
        )
        for code, prefix in LOCALES:
            head = f"{prefix}/" if prefix else ""
            for rel, template, title in (
                ("", "index.html", "home"),
                ("about/", "about.html", "about"),
            ):
                alternates = _alternates(rel)
                if self.break_alternates and code == "en" and rel == "":
                    alternates = {"en": alternates["en"]}
                pages.append(
                    Page(
                        meta=PageMeta(
                            title=title,
                            path=f"{head}{rel}index.html",
                            description="d",
                            structured_data=jsonld,
                            locale=code,
                            alternates=alternates,
                        ),
                        template=template,
                        context={"today": now},
                        trust=trust,
                    )
                )
        pages.append(
            Page(
                meta=PageMeta(title="404", path="404.html", noindex=True, locale="ja"),
                template="404.html",
                context={"today": now},
                trust=trust,
            )
        )
        return pages

    def search_index(self, ws: Workspace) -> Any:
        return {"ja.json": [], "en.json": [], "zh-Hant.json": []}

    def redirects(self, ws: Workspace) -> Sequence[Redirect]:
        return []

    def eval_dir(self, ws: Workspace) -> Path | None:
        return None


service = PolyService()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "base.html").write_text(BASE_TEMPLATE, encoding="utf-8")
    (templates / "index.html").write_text(INDEX_TEMPLATE, encoding="utf-8")
    (templates / "about.html").write_text(ABOUT_TEMPLATE, encoding="utf-8")
    (templates / "404.html").write_text(NOTFOUND_TEMPLATE, encoding="utf-8")
    i18n = tmp_path / "i18n"
    i18n.mkdir()
    for name, text in CATALOGS.items():
        (i18n / name).write_text(text, encoding="utf-8")
    return tmp_path


def _build(root: Path) -> tuple[Path, Any]:
    ws = Workspace.open(root)
    ws.ensure_dirs()
    svc = PolyService()
    result = SiteBuilder(ws, svc, now=datetime(2026, 9, 12, 6, 10)).build()
    return ws.dist_dir, result


def test_pages_land_under_their_locale_prefix(root: Path) -> None:
    dist, result = _build(root)
    for rel in ("index.html", "about/index.html", "en/index.html", "zh-hant/about/index.html"):
        assert (dist / rel).is_file(), rel
    assert result.pages == 7


def test_each_page_declares_every_language_version(root: Path) -> None:
    dist, _ = _build(root)
    html = (dist / "en" / "index.html").read_text(encoding="utf-8")
    links = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', html))
    assert links == {
        "ja": "https://poly.example/",
        "en": "https://poly.example/en/",
        "zh-Hant": "https://poly.example/zh-hant/",
        "x-default": "https://poly.example/",  # 既定ロケールを指す
    }


def test_html_lang_and_og_locale_follow_the_page(root: Path) -> None:
    dist, _ = _build(root)
    zh = (dist / "zh-hant" / "index.html").read_text(encoding="utf-8")
    assert '<html lang="zh-Hant">' in zh
    assert '<meta property="og:locale" content="zh_TW">' in zh
    ja = (dist / "index.html").read_text(encoding="utf-8")
    assert '<meta property="og:locale" content="ja_JP">' in ja


def test_facts_are_rendered_in_each_locale(root: Path) -> None:
    """同じ構造化データから、言語ごとに数字と日付の書き方だけを変える。"""
    dist, _ = _build(root)
    fees = {
        code: re.search(r'<p class="fee">([^<]+)</p>', (dist / rel).read_text(encoding="utf-8"))
        for code, rel in (
            ("ja", "index.html"),
            ("en", "en/index.html"),
            ("zh", "zh-hant/index.html"),
        )
    }
    assert fees["ja"] and fees["ja"].group(1) == "2,100円"
    assert fees["en"] and fees["en"].group(1) == "¥2,100"
    assert fees["zh"] and fees["zh"].group(1) == "2,100日圓"

    days = {
        code: re.search(r'<p class="day">([^<]+)</p>', (dist / rel).read_text(encoding="utf-8"))
        for code, rel in (("ja", "index.html"), ("en", "en/index.html"))
    }
    assert days["ja"] and days["ja"].group(1) == "9月12日（土）"
    assert days["en"] and days["en"].group(1) == "Sat, 12 Sep"


def test_trust_block_is_localized_but_keeps_its_marker(root: Path) -> None:
    dist, _ = _build(root)
    en = (dist / "en" / "index.html").read_text(encoding="utf-8")
    assert "data-sitemill-trust" in en  # ビルド時検査が見る印は言語に関わらず必要
    assert "About this information" in en and "1,234 records" in en
    assert "12 September 2026 06:10" in en
    zh = (dist / "zh-hant" / "index.html").read_text(encoding="utf-8")
    assert "關於這些資訊" in zh and "1,234 筆" in zh


def test_contact_url_becomes_a_link_with_a_localized_label(root: Path) -> None:
    """連絡先が URL のときはリンクにする（v0.1.1 の修正）。文字列は各言語のカタログから。"""
    dist, _ = _build(root)
    en = (dist / "en" / "index.html").read_text(encoding="utf-8")
    assert (
        '<a href="https://forms.example/contact" rel="noopener" target="_blank">Contact form</a>'
    ) in en
    ja = (dist / "index.html").read_text(encoding="utf-8")
    assert ">お問い合わせフォーム</a>" in ja  # site.toml の contact_label ではなくカタログの文言
    # 繁体字はこのキーが未翻訳なので、既定ロケールの文言に落ちる（他の未翻訳と同じ扱い）
    zh = (dist / "zh-hant" / "index.html").read_text(encoding="utf-8")
    assert ">お問い合わせフォーム</a>" in zh


def test_untranslated_keys_fall_back_and_are_reported(root: Path) -> None:
    """未翻訳でも公開は止めない。ただし何が残っているかは実行レポートに出す。"""
    dist, result = _build(root)
    zh = (dist / "zh-hant" / "index.html").read_text(encoding="utf-8")
    assert "今日行ける日本" in zh  # 既定ロケール（日本語）に落ちている
    warning = next((w for w in result.warnings if "zh-Hant" in w), None)
    assert warning is not None
    assert "未翻訳" in warning and "home.note" in warning


def test_sitemap_lists_the_language_versions(root: Path) -> None:
    dist, _ = _build(root)
    sitemap = (dist / "sitemap.xml").read_text(encoding="utf-8")
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in sitemap
    assert '<xhtml:link rel="alternate" hreflang="en" href="https://poly.example/en/"/>' in sitemap


def test_one_sided_alternates_stop_the_build(root: Path) -> None:
    """相手が指し返さない hreflang は検索エンジンに無視される。ビルドで止める。"""
    ws = Workspace.open(root)
    ws.ensure_dirs()
    svc = PolyService()
    svc.break_alternates = True
    with pytest.raises(BuildError, match="同じ対応を持っていない"):
        SiteBuilder(ws, svc, now=datetime(2026, 9, 12, 6, 10)).build()


def test_missing_alternates_stop_the_build(root: Path) -> None:
    class NoAlternates(PolyService):
        def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
            pages = super().pages(ws, now=now)
            for page in pages:
                page.meta.alternates = {}
            return pages

    ws = Workspace.open(root)
    ws.ensure_dirs()
    with pytest.raises(BuildError, match="alternates"):
        SiteBuilder(ws, NoAlternates(), now=datetime(2026, 9, 12, 6, 10)).build()


def test_template_that_forgets_hreflang_stops_the_build(root: Path) -> None:
    """テンプレートが head_meta に hreflangs を渡し忘れたら気づけるようにする。"""
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    (root / "templates" / "base.html").write_text(
        base.replace(", hreflangs=hreflangs", ""), encoding="utf-8"
    )
    ws = Workspace.open(root)
    ws.ensure_dirs()
    with pytest.raises(BuildError, match="hreflang"):
        SiteBuilder(ws, PolyService(), now=datetime(2026, 9, 12, 6, 10)).build()
