"""ページ分類器（ADR 0007）。候補ページを 一覧/詳細/SPA/第三者/非物件 に確信度つきで分類する。

巡回の可否はこの分類だけでは決めない。運営主体ゲート（サービス側）と組み合わせて使う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from selectolax.parser import HTMLParser

from sitemill.classify.platforms import PlatformRegistry
from sitemill.diff.normalize import page_text

_PRICE = re.compile(r"\d[\d,]*\s*(?:億|万)?\s*円")
_LISTING_NO = re.compile(
    r"(?:物件番号|登録番号|管理番号|物件No|No|NO|№)\s*[.:：]?\s*[A-Za-z]?\d{1,5}"
    r"|(?<![A-Za-z0-9])[A-Z]{1,3}[-‐]?\d{2,5}(?![A-Za-z0-9])"
)
_FIELD_LABELS = (
    ("所在地", re.compile(r"所在地|住所")),
    ("土地面積", re.compile(r"敷地面積|土地面積")),
    ("延床面積", re.compile(r"延床面積|建物面積|床面積")),
    ("築年", re.compile(r"築年|建築年|建築年月|築年月")),
    ("間取り", re.compile(r"間取り|間取")),
    ("構造", re.compile(r"構造")),
)
_TABLE_HEADERS = re.compile(r"価格|価額")
_SPA_MARKERS = (
    re.compile(r'id=["\'](?:app|root|__nuxt|__next)["\']'),
    re.compile(r"enable\s*JavaScript|JavaScript\s*を?\s*有効", re.I),
    re.compile(r"/(?:js|css)/chunk-|webpackJsonp|__NUXT__|data-reactroot|ng-version"),
)


class PageClass(StrEnum):
    listing_index = "listing_index"
    listing_detail = "listing_detail"
    spa = "spa"
    third_party = "third_party"
    not_listing = "not_listing"


PAGE_CLASS_LABELS = {
    PageClass.listing_index: "物件一覧（静的）",
    PageClass.listing_detail: "物件詳細（静的）",
    PageClass.spa: "JavaScript 描画（未対応）",
    PageClass.third_party: "第三者プラットフォーム",
    PageClass.not_listing: "物件ページではない",
}


@dataclass
class ClassifiedPage:
    url: str
    page_class: PageClass
    confidence: float
    signals: dict[str, int | bool | str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    @property
    def crawlable_kind(self) -> str | None:
        """巡回対象なら PageKind 値（listing_index / listing_detail）、それ以外は None。"""
        if self.page_class is PageClass.listing_index:
            return "listing_index"
        if self.page_class is PageClass.listing_detail:
            return "listing_detail"
        return None

    @property
    def label(self) -> str:
        return PAGE_CLASS_LABELS[self.page_class]


def _clip(x: float) -> float:
    return max(0.05, min(0.99, round(x, 2)))


def classify_page(
    html: str, url: str, *, platforms: PlatformRegistry, content_selector: str | None = None
) -> ClassifiedPage:
    # 1) 第三者プラットフォーム: ホストで確定
    host = platforms.match(url)
    if host is not None:
        return ClassifiedPage(
            url=url,
            page_class=PageClass.third_party,
            confidence=0.99,
            signals={"platform_host": host},
            evidence=[f"民間プラットフォームのホスト {host}"],
        )

    text = page_text(html, content_selector)
    body_len = len(text)
    tree = HTMLParser(html)
    script_count = len(tree.css("script"))
    spa_markers = sum(1 for p in _SPA_MARKERS if p.search(html))

    # 2) SPA: 本文が薄く、スクリプトが多く、フレームワークの痕跡がある
    if body_len < 400 and spa_markers >= 1 and script_count >= 3:
        return ClassifiedPage(
            url=url,
            page_class=PageClass.spa,
            confidence=_clip(0.6 + 0.15 * spa_markers),
            signals={"body_len": body_len, "scripts": script_count, "spa_markers": spa_markers},
            evidence=["本文が JavaScript で描画され、静的 HTML に物件が無い"],
        )

    price_count = len(_PRICE.findall(text))
    listing_no_count = len(_LISTING_NO.findall(text))
    fields = [name for name, pat in _FIELD_LABELS if pat.search(text)]
    field_count = len(fields)
    has_table_price = bool(_TABLE_HEADERS.search(html) and re.search(r"所在地|住所", html))

    signals: dict[str, int | bool | str] = {
        "price_count": price_count,
        "listing_no_count": listing_no_count,
        "field_count": field_count,
        "body_len": body_len,
        "table_price": has_table_price,
    }

    # 3) 一覧: 価格表記が複数 or 物件番号が複数
    index_score = 0.0
    if price_count >= 3:
        index_score = 0.55 + min(price_count, 20) * 0.02
    elif price_count >= 1 and listing_no_count >= 2:
        index_score = 0.5 + listing_no_count * 0.02

    # 4) 詳細: 単一物件（価格が少なく、項目ラベルがそろう）
    detail_score = 0.0
    if field_count >= 3 and price_count >= 1 and price_count <= 3 and listing_no_count <= 3:
        detail_score = 0.5 + field_count * 0.08

    # 一覧と詳細が競合したら、価格表記の多さで一覧を優先
    if index_score and price_count >= 6:
        detail_score *= 0.5
    if detail_score and field_count >= 5 and price_count <= 2:
        index_score *= 0.6

    if index_score >= detail_score and index_score > 0:
        conf = _clip(index_score - (detail_score * 0.3))
        return ClassifiedPage(
            url=url,
            page_class=PageClass.listing_index,
            confidence=conf,
            signals=signals,
            evidence=[f"価格表記 {price_count} 件・物件番号 {listing_no_count} 件"],
        )
    if detail_score > 0:
        conf = _clip(detail_score - (index_score * 0.3))
        return ClassifiedPage(
            url=url,
            page_class=PageClass.listing_detail,
            confidence=conf,
            signals=signals,
            evidence=[f"項目ラベル {'/'.join(fields)}・価格 {price_count} 件"],
        )

    # 5) それ以外
    return ClassifiedPage(
        url=url,
        page_class=PageClass.not_listing,
        confidence=_clip(0.55 + (0.1 if price_count == 0 else 0)),
        signals=signals,
        evidence=["物件の繰り返しも項目ラベルのそろいも見られない"],
    )
