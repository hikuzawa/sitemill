"""公開前チェック（ADR 0012）。ページ個別・サイト全体を検査し、欠ければビルドを止める。

ページ個別: canonical・OGP・JSON-LD の妥当性（noindex ページは索引されないため免除）。
サイト全体: robots.txt / sitemap.xml / 404 / /about（運営者欄と免責）/ 解析タグの出力条件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from selectolax.parser import HTMLParser

_CANONICAL = re.compile(r"""<link[^>]+rel=["']canonical["']""", re.I)
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


def check_page_html(html: str, *, path: str, noindex: bool = False) -> list[str]:
    """1 ページ分の HTML を検査して問題点の一覧を返す。空なら合格。"""
    problems: list[str] = []
    if not noindex:
        if not _CANONICAL.search(html):
            problems.append(f"{path}: canonical リンクが無い")
        missing = [k for k in _OGP_REQUIRED if not _has_og(html, k)]
        if missing:
            problems.append(f"{path}: OGP が不足（{', '.join(missing)}）")
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
