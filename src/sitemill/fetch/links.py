"""HTML からのリンク抽出とホスト制限。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

_SKIP_PREFIX = ("mailto:", "tel:", "javascript:", "#", "data:")


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass(frozen=True)
class LinkRow:
    """リンクと、その「行」のテキスト。表や箇条書きのリンク集を読むために使う。"""

    url: str
    text: str  # アンカー文字列。空なら img の alt / a の title
    context: str  # 直近の行（tr / li / dd / p など）のテキスト。長すぎるときは空
    heading: str = ""  # そのリンクの直前にある見出し（h1〜h6）


# 「行」とみなす要素。tr や li を td より優先したいので、短いものの中で最も広いものを採る
_ROW_TAGS = ("tr", "li", "dd", "dt", "td", "th", "p", "div")
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
MAX_CONTEXT = 120
MAX_HEADING = 80


def extract_links(html: str, base_url: str) -> list[Link]:
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    if base is not None and base.attributes.get("href"):
        base_url = urljoin(base_url, base.attributes["href"])
    out: list[Link] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_PREFIX):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(Link(url=url, text=a.text(separator=" ", strip=True)))
    return out


def _anchor_text(a: Node) -> str:
    text = a.text(separator=" ", strip=True)
    if text:
        return text
    img = a.css_first("img[alt]")
    if img is not None:
        alt = (img.attributes.get("alt") or "").strip()
        if alt:
            return alt
    return (a.attributes.get("title") or "").strip()


def _row_context(a: Node, max_context: int) -> str:
    """アンカーを含む「行」のテキスト。短いものの中で最も情報量の多いものを採る。"""
    texts: list[str] = []
    node, depth = a.parent, 0
    while node is not None and depth < 6:
        if node.tag in _ROW_TAGS:
            t = node.text(separator=" ", strip=True)
            if t and len(t) <= max_context:
                texts.append(t)
        node, depth = node.parent, depth + 1
    return max(texts, key=len, default="")


def extract_link_rows(html: str, base_url: str, *, max_context: int = MAX_CONTEXT) -> list[LinkRow]:
    """リンクを行のテキストつきで返す。名前が alt や隣の列にあるリンク集を読むため。"""
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    if base is not None and base.attributes.get("href"):
        base_url = urljoin(base_url, base.attributes["href"])
    out: list[LinkRow] = []
    seen: set[str] = set()
    heading = ""
    for node in tree.root.traverse(include_text=False) if tree.root else ():
        if node.tag in _HEADING_TAGS:
            heading = node.text(separator=" ", strip=True)[:MAX_HEADING]
            continue
        if node.tag != "a":
            continue
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_PREFIX):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(
            LinkRow(
                url=url,
                text=_anchor_text(node),
                context=_row_context(node, max_context),
                heading=heading,
            )
        )
    return out


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def host_allowed(url: str, allow_hosts: list[str]) -> bool:
    host = host_of(url)
    if not host:
        return False
    for allowed in allow_hosts:
        a = allowed.lower().lstrip(".")
        if host == a or host.endswith("." + a):
            return True
    return False
