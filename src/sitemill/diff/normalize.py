"""HTML の本文化と正規化。差分検知のハッシュと LLM への入力の両方で使う（ADR 0003, 0004）。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from selectolax.parser import HTMLParser, Node

NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "canvas",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
)
MAIN_SELECTORS = (
    "main",
    "[role=main]",
    "#main",
    "#contents",
    "#content",
    "#main-content",
    ".main-content",
    "#mainContents",
    "#container .contents",
)
BLOCK_TAGS = {
    "p",
    "div",
    "li",
    "tr",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "caption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "dl",
    "dt",
    "dd",
    "ul",
    "ol",
    "blockquote",
    "pre",
    "hr",
    "address",
    "figure",
    "figcaption",
    "main",
    "body",
    "details",
    "summary",
}
CELL_TAGS = {"td", "th"}
_WS_ALL = re.compile(r"\s+")
_LINE_WS = re.compile(r"[ \t　]+")
_MANY_NL = re.compile(r"\n{3,}")


def select_main(html: str, selector: str | None = None) -> Node:
    """本文らしいノードを返す。指定セレクタ → 定番のセレクタ → 単一の article → body。"""
    tree = HTMLParser(html)
    body = tree.body or tree.root
    if body is None:  # pragma: no cover - 空文字列など
        return HTMLParser("<html><body></body></html>").body  # type: ignore[return-value]
    body_len = len(body.text(separator=" ", strip=True))
    candidates: list[Node] = []
    if selector:
        node = tree.css_first(selector)
        if node is not None:
            candidates.append(node)
    for sel in MAIN_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            candidates.append(node)
    articles = tree.css("article")
    if len(articles) == 1:
        candidates.append(articles[0])
    for node in candidates:
        if body_len == 0 or len(node.text(separator=" ", strip=True)) >= body_len * 0.3:
            return node
    return body


def _strip_noise(root: Node) -> Node:
    """飾りの要素を落とす。ただし本文ごと消してしまう form は残す。

    自治体サイトに多い ASP.NET 系の CMS は、ページ全体を 1 つの `<form>` で囲む。
    検索窓のつもりで form を捨てると本文が丸ごと消え、物件一覧が 0 件になる。
    """
    root_len = len(root.text(separator=" ", strip=True))
    for tag in NOISE_TAGS:
        for n in root.css(tag):
            if (
                tag == "form"
                and root_len
                and len(n.text(separator=" ", strip=True)) >= root_len * 0.5
            ):
                continue
            n.decompose()
    return root


def _walk(node: Node, out: list[str]) -> None:
    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text":
            out.append(child.text_content or "")
        elif tag == "br":
            out.append("\n")
        elif tag in CELL_TAGS:
            _walk(child, out)
            out.append(" | ")
        elif tag in BLOCK_TAGS:
            out.append("\n")
            _walk(child, out)
            out.append("\n")
        else:
            _walk(child, out)


def page_text(html: str, selector: str | None = None) -> str:
    """LLM に渡すための本文テキスト。ブロック要素で改行し、表のセルは ' | ' で区切る。"""
    root = _strip_noise(select_main(html, selector))
    parts: list[str] = []
    _walk(root, parts)
    lines: list[str] = []
    for raw in "".join(parts).split("\n"):
        line = _LINE_WS.sub(" ", raw).strip()
        while line.endswith("|"):
            line = line[:-1].rstrip()
        lines.append(line)
    text = "\n".join(lines)
    text = _MANY_NL.sub("\n\n", text).strip()
    return unicodedata.normalize("NFKC", text)


def squash(text: str) -> str:
    """引用照合とハッシュのための正規化: NFKC にして空白をすべて取り除く。"""
    return _WS_ALL.sub("", unicodedata.normalize("NFKC", text))


def normalize_for_hash(
    html: str, selector: str | None = None, ignore_patterns: Iterable[str] = ()
) -> str:
    """差分検知用の正規化本文。ノイズ要素を除き、指定パターンを消し、空白を落とす。"""
    text = page_text(html, selector)
    for pattern in ignore_patterns:
        text = re.sub(pattern, "", text)
    return squash(text)
