"""公開前チェック（ADR 0012）。ページ個別・サイト全体を検査し、欠ければビルドを止める。

ページ個別: canonical・OGP・JSON-LD の妥当性（noindex ページは索引されないため免除）。
サイト全体: robots.txt / sitemap.xml / 404 / /about（運営者欄と免責）/ 解析タグの出力条件。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from selectolax.parser import HTMLParser

from sitemill.i18n.locale import LocaleConfig
from sitemill.models import Page

_CANONICAL = re.compile(r"""<link[^>]+rel=["']canonical["']""", re.I)
_HREFLANG = re.compile(r"""<link[^>]+hreflang=["']""", re.I)
_OGP_REQUIRED = ("og:title", "og:type", "og:url")
_JSONLD = re.compile(
    r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", re.I | re.S
)
ANALYTICS_BEACON = "static.cloudflareinsights.com/beacon.min.js"

# 公開に必須のサイト全体ファイルと、その説明。
REQUIRED_FILES = {
    "robots.txt": "robots.txt",
    "sitemap.xml": "sitemap.xml",
    "404.html": "404 ページ",
    "about/index.html": "運営者情報ページ (/about)",
}


def _has_og(html: str, prop: str) -> bool:
    return f'property="{prop}"' in html or f"property='{prop}'" in html


def check_page_html(
    html: str, *, path: str, noindex: bool = False, expect_hreflang: bool = False
) -> list[str]:
    """1 ページ分の HTML を検査して問題点の一覧を返す。空なら合格。"""
    problems: list[str] = []
    if not noindex:
        if not _CANONICAL.search(html):
            problems.append(f"{path}: canonical リンクが無い")
        missing = [k for k in _OGP_REQUIRED if not _has_og(html, k)]
        if missing:
            problems.append(f"{path}: OGP が不足（{', '.join(missing)}）")
    if expect_hreflang and not _HREFLANG.search(html):
        # 各言語版があるのに hreflang を出していない。テンプレートが head_meta に
        # hreflangs を渡し忘れている（ADR 0016）
        problems.append(f"{path}: 各言語版があるのに hreflang が出力されていない")
    blocks = _JSONLD.findall(html)
    if not noindex and not blocks:
        problems.append(f"{path}: 構造化データ（JSON-LD）が無い")
    for i, block in enumerate(blocks, 1):
        try:
            json.loads(block.strip())
        except json.JSONDecodeError as e:
            problems.append(f"{path}: JSON-LD #{i} が不正な JSON（{e}）")
    return problems


def visible_text_and_contacts(html: str) -> str:
    """PII 走査用に、可視テキストと mailto:/tel: リンクの値を連結して返す。"""
    tree = HTMLParser(html)
    for tag in tree.css("script, style"):
        tag.decompose()
    node = tree.body or tree.root
    text = node.text(separator=" ") if node else ""
    contacts: list[str] = []
    for el in tree.css("a[href]"):
        href = el.attributes.get("href") or ""
        if href.startswith(("mailto:", "tel:")):
            contacts.append(href.split(":", 1)[1])
    return text + " " + " ".join(contacts)


def check_pages(
    pages: Sequence[Page], *, locales: Sequence[LocaleConfig], default_code: str
) -> list[str]:
    """ページ一覧を対象に、ロケールと各言語版の対応づけを検査する（ADR 0016）。

    HTML を描画する前に呼ぶ。片側だけの hreflang（相手が自分を指し返さない）は
    検索エンジンに無視されるので、対応が相互になっているかまで見る。
    """
    known = {lc.code for lc in locales}
    multilingual = len(known) > 1
    problems: list[str] = []
    by_key: dict[tuple[str, str], Page] = {}
    for page in pages:
        code = page.meta.locale or default_code
        if code not in known:
            problems.append(f"{page.meta.path}: site.toml の [[locales]] に無いロケール（{code}）")
            continue
        by_key[(code, page.meta.url_path)] = page

    for page in pages:
        meta = page.meta
        code = meta.locale or default_code
        if code not in known:
            continue
        if not meta.alternates:
            if multilingual and not meta.noindex:
                problems.append(
                    f"{meta.path}: 多言語サイトなのに alternates（各言語版の対応）が空。"
                    "1 言語しか無いページでも自分自身を入れる"
                )
            continue
        if meta.alternates.get(code) != meta.url_path:
            problems.append(
                f"{meta.path}: alternates が自分自身（{code}）を指していない"
                f"（{meta.alternates.get(code)!r} ≠ {meta.url_path!r}）"
            )
        for other_code, other_path in meta.alternates.items():
            if other_code not in known:
                problems.append(f"{meta.path}: alternates に未知のロケール（{other_code}）")
                continue
            other = by_key.get((other_code, other_path))
            if other is None:
                problems.append(
                    f"{meta.path}: alternates の指す先のページが無い（{other_code}: {other_path}）"
                )
            elif other.meta.alternates != meta.alternates:
                problems.append(
                    f"{meta.path}: {other.meta.path} が同じ対応を持っていない"
                    "（hreflang は相互に指し合う必要がある）"
                )
    return problems


def _html_files(dist: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(dist.rglob("*.html")):
        out[p.relative_to(dist).as_posix()] = p.read_text(encoding="utf-8")
    return out


def check_site(dist: Path, *, analytics_token: str | None) -> list[str]:
    """dist を対象にサイト全体の公開前チェックを行う。"""
    problems: list[str] = []
    for rel, label in REQUIRED_FILES.items():
        if not (dist / rel).is_file():
            problems.append(f"{label} が生成されていない（{rel}）")

    robots = dist / "robots.txt"
    if robots.is_file() and "Sitemap:" not in robots.read_text(encoding="utf-8"):
        problems.append("robots.txt に Sitemap 行が無い")

    about = dist / "about" / "index.html"
    if about.is_file():
        t = about.read_text(encoding="utf-8")
        if "運営者" not in t:
            problems.append("/about に運営者欄が無い")
        if "免責" not in t:
            problems.append("/about に免責の記載が無い")

    htmls = _html_files(dist)
    beacon_pages = [rel for rel, txt in htmls.items() if ANALYTICS_BEACON in txt]
    if analytics_token:
        if not beacon_pages:
            problems.append("解析トークンが設定されているのに解析タグがどのページにも無い")
    elif beacon_pages:
        problems.append(f"解析トークン未設定なのに解析タグが出力されている: {beacon_pages[:3]}")
    return problems
