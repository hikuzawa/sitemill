"""「一覧らしさ」の決定的スコアリング（ADR 0011 追記）。

候補ページのうち「物件一覧」を選ぶため、次のシグナルを数える:
- 物件行の数（価格と、面積・築年・間取り・所在地などの物件属性を同じブロックに持つ行）
- 価格の文脈（物件価格か、補助金・料金など物件でない金額か）
- 価格・所在地の列（表の見出し）
- ページネーション（現在のページ番号、1 ページ目の URL、辿るための URL パターン）
- 成約済みなど「募集していない」印の割合

LLM は使わない。改修補助金・農業体験ツアー・制度案内のような「万円は出てくるが一覧ではない」
ページを一覧と取り違えない（akiya-atlas の栄村・小川村・生坂村・小布施町・坂出市の事例）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from sitemill.diff.normalize import page_text

_PRICE = re.compile(r"\d[\d,]*\s*(?:億|万)?\s*円")
_LISTING_NO = re.compile(
    r"(?:物件番号|登録番号|管理番号|物件No|No|NO|№)\s*[.:：]?\s*[A-Za-z]?\d{1,5}"
    r"|(?<![A-Za-z0-9])[A-Z]{1,3}[-‐]?\d{2,5}(?![A-Za-z0-9])"
)
# 物件の属性語（価格と同じ行・ブロックにあれば「物件行」）
_PROPERTY_CTX = re.compile(
    r"㎡|m2|平米|坪|築|間取|[0-9１-９]+\s*[LDKSldks]{1,4}|戸建|平屋|階建|古民家|土地|宅地|"
    r"所在地|所在|地区|字|物件|売買|売却|賃貸|家賃|賃料|月額|価格|物置|庭|駐車"
)
# 物件でない金額の文脈（補助・料金・税など）
_NON_PROPERTY_CTX = re.compile(
    r"補助|助成|上限|限度|交付|旅行代金|参加費|料金|以内|手数料|報酬|支給|奨励|給付|"
    r"定額|税|保険|年収|所得|割引"
)
_SUBSIDY = re.compile(
    r"補助金|助成金|交付|申請書|様式第|要綱|対象者|対象経費|補助率|上限額|限度額|補助対象|"
    r"ツアー|募集要項|旅行代金|参加費|申請期間|受付期間|申請方法|チラシ"
)
_CLOSED = re.compile(r"成約済|ご成約|売約済|契約済|商談中|募集終了|掲載終了|申込済")
_PRICE_COL = re.compile(r"価格|価額|賃料|家賃")
_ADDR_COL = re.compile(r"所在地|住所|地区|所在|場所|エリア")
_NEXT_TEXT = re.compile(r"次へ|次のページ|次ページ|次の\s*\d+\s*件|»|>>|›|next|older", re.I)
_PAGE_PATH = re.compile(r"/page/(\d+)/?$")
_PAGE_QUERY_KEYS = ("page", "p", "pg", "pagenum", "page_no", "pageno")
_ROW_TAGS = "tr, li, article, section, dl, div, p"
_ROW_MAX_CHARS = 600


@dataclass(frozen=True)
class PaginationInfo:
    is_paginated: bool = False
    current_page: int = 1
    first_page_url: str | None = None  # 現在が 2 ページ目以降のとき、1 ページ目の URL
    follow_pattern: str | None = None  # 2 ページ目以降を辿る絶対 URL の正規表現


@dataclass
class ListingScore:
    score: float = 0.0
    rows: int = 0
    property_prices: int = 0
    other_prices: int = 0
    price_count: int = 0
    area_count: int = 0
    listing_no_count: int = 0
    subsidy_hits: int = 0
    closed_hits: int = 0
    has_price_col: bool = False
    has_address_col: bool = False
    pagination: PaginationInfo = field(default_factory=PaginationInfo)
    evidence: list[str] = field(default_factory=list)

    @property
    def is_listing(self) -> bool:
        """一覧として扱ってよいか（物件行が複数、または物件文脈の価格が複数で補助金ページでない）。"""
        if self.subsidy_dominant:
            return False
        return self.rows >= 2 or (self.property_prices >= 3 and self.rows >= 1)

    @property
    def subsidy_dominant(self) -> bool:
        """補助金・ツアー・制度案内の語が多く、物件行がほぼ無い。"""
        return self.subsidy_hits >= 4 and self.rows <= 1

    @property
    def closed_ratio(self) -> float:
        base = max(self.rows, self.property_prices, 1)
        return min(1.0, self.closed_hits / base)

    def as_signals(self) -> dict[str, int | bool | str | float]:
        return {
            "listing_score": round(self.score, 2),
            "listing_rows": self.rows,
            "property_prices": self.property_prices,
            "other_prices": self.other_prices,
            "subsidy_hits": self.subsidy_hits,
            "closed_hits": self.closed_hits,
            "price_col": self.has_price_col,
            "address_col": self.has_address_col,
            "paginated": self.pagination.is_paginated,
            "page": self.pagination.current_page,
        }


def _node_text(node: Node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True))


def _count_rows(tree: HTMLParser) -> tuple[int, int]:
    """価格と物件属性を同じブロックに持つ「物件行」を数える。入れ子は本文の重複で除く。"""
    texts: set[str] = set()
    closed = 0
    for node in tree.css(_ROW_TAGS):
        text = _node_text(node)
        if not text or len(text) > _ROW_MAX_CHARS:
            continue
        if not _PRICE.search(text):
            continue
        if not _PROPERTY_CTX.search(text):
            continue
        if _NON_PROPERTY_CTX.search(text) and not re.search(r"㎡|m2|平米|坪|築|間取|LDK", text):
            continue  # 補助金の上限などの金額行
        if text in texts:
            continue
        # 親が子と同じ本文を持つ入れ子は 1 行と数える。複数行を含む親は長さ上限で外れる
        texts.add(text)
        if _CLOSED.search(text):
            closed += 1
    return len(texts), closed


def _count_line_rows(text: str) -> int:
    n = 0
    for line in text.split("\n"):
        if len(line) > _ROW_MAX_CHARS or not _PRICE.search(line):
            continue
        if _PROPERTY_CTX.search(line) and not (
            _NON_PROPERTY_CTX.search(line) and not re.search(r"㎡|m2|平米|坪|築|間取|LDK", line)
        ):
            n += 1
    return n


def _price_contexts(text: str) -> tuple[int, int]:
    prop = other = 0
    for m in _PRICE.finditer(text):
        window = text[max(0, m.start() - 40) : m.end() + 40]
        if _NON_PROPERTY_CTX.search(window):
            other += 1
        elif _PROPERTY_CTX.search(window):
            prop += 1
    return prop, other


def _columns(tree: HTMLParser) -> tuple[bool, bool]:
    price = addr = False
    for table in tree.css("table"):
        headers = [_node_text(th) for th in table.css("th")]
        if not headers:
            first = table.css_first("tr")
            headers = [_node_text(td) for td in first.css("td")] if first else []
        joined = " | ".join(headers)
        if _PRICE_COL.search(joined):
            price = True
        if _ADDR_COL.search(joined):
            addr = True
    return price, addr


def _strip_page(url: str) -> tuple[str, int]:
    """URL からページ番号を取り、(1 ページ目の URL, 現在のページ番号) を返す。"""
    parts = urlsplit(url)
    m = _PAGE_PATH.search(parts.path)
    if m:
        path = parts.path[: m.start()] + "/"
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, "")), int(m.group(1))
    query = parse_qsl(parts.query, keep_blank_values=True)
    for key, value in query:
        if key.lower() in _PAGE_QUERY_KEYS and value.isdigit():
            rest = [(k, v) for k, v in query if k != key]
            return (
                urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(rest), "")),
                int(value),
            )
    return url, 1


def detect_pagination(tree: HTMLParser, url: str) -> PaginationInfo:
    """現在のページ番号と、2 ページ目以降を辿るための URL パターンを求める。"""
    first_url, current = _strip_page(url)
    page_links: list[str] = []
    has_next = bool(tree.css_first('a[rel="next"], link[rel="next"]'))
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = _node_text(a)
        _, n = _strip_page(href)
        if n > 1:
            page_links.append(href)
        elif _NEXT_TEXT.search(text):
            has_next = True
    paginated = current > 1 or bool(page_links) or has_next
    pattern: str | None = None
    if page_links or current > 1:
        base = urlsplit(first_url)
        prefix = re.escape(f"{base.scheme}://{base.netloc}{base.path.rstrip('/')}")
        sample = page_links[0] if page_links else url
        if _PAGE_PATH.search(urlsplit(sample).path):
            pattern = prefix + r"/page/\d+/?$"
        else:
            pattern = prefix + r"/?\?(?:.*&)?(?:page|p|pg|pagenum|page_no|pageno)=\d+"
    return PaginationInfo(
        is_paginated=paginated,
        current_page=current,
        first_page_url=first_url if current > 1 else None,
        follow_pattern=pattern,
    )


def listing_score(html: str, url: str, *, content_selector: str | None = None) -> ListingScore:
    tree = HTMLParser(html)
    text = page_text(html, content_selector)
    rows, closed_rows = _count_rows(tree)
    rows = max(rows, _count_line_rows(text))
    prop, other = _price_contexts(text)
    price_count = len(_PRICE.findall(text))
    has_price_col, has_addr_col = _columns(tree)
    ls = ListingScore(
        rows=rows,
        property_prices=prop,
        other_prices=other,
        price_count=price_count,
        area_count=len(re.findall(r"\d[\d,.]*\s*(?:㎡|m2|平米|坪)", text)),
        listing_no_count=len(_LISTING_NO.findall(text)),
        subsidy_hits=len(_SUBSIDY.findall(text)),
        closed_hits=max(closed_rows, len(_CLOSED.findall(text))),
        has_price_col=has_price_col,
        has_address_col=has_addr_col,
        pagination=detect_pagination(tree, url),
    )
    score = min(rows, 10) * 0.08 + min(prop, 10) * 0.03 + min(ls.listing_no_count, 5) * 0.02
    score += 0.1 if has_price_col else 0.0
    score += 0.1 if has_addr_col else 0.0
    if ls.subsidy_dominant:
        score *= 0.3
    elif other > prop and rows <= 1:
        score *= 0.5
    ls.score = max(0.0, min(1.0, round(score, 3)))
    ls.evidence.append(
        f"物件行 {rows}・物件価格 {prop}・非物件価格 {other}・補助金語 {ls.subsidy_hits}"
        + ("・価格列" if has_price_col else "")
        + ("・所在地列" if has_addr_col else "")
        + (f"・{ls.pagination.current_page}ページ目" if ls.pagination.current_page > 1 else "")
    )
    return ls
