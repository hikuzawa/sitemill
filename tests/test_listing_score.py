"""一覧らしさスコアと分類器の回帰テスト（akiya-atlas の誤採用 5 事例 + 正解 2 ページ）。

fixtures/html/classify/SOURCES.md に出典。ネットワークは使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sitemill.classify import PageClass, PlatformRegistry, classify_page, listing_score
from sitemill.classify.listing_score import detect_pagination
from sitemill.fetch.decode import decode_html

FIX = Path(__file__).parent / "fixtures" / "html" / "classify"
CASES = {
    # ファイル: (URL, 期待する分類)
    "ikusaka_tour.html": (
        "https://www.village.ikusaka.nagano.jp/gyousei/sinkouka/aki_nougyou_taiken2026.html",
        PageClass.not_listing,
    ),
    "obuse_subsidy.html": (
        "https://www.town.obuse.nagano.jp/docs/36516.html",
        PageClass.not_listing,
    ),
    "sakaide_subsidy.html": (
        "https://www.city.sakaide.lg.jp/soshiki/seisaku/akiyakaisyuu.html",
        PageClass.not_listing,
    ),
    "sakae_guide_834.html": (
        "http://www.vill.sakae.nagano.jp/docs/834.html",
        PageClass.not_listing,
    ),
    "sakae_list_1028.html": (
        "http://www.vill.sakae.nagano.jp/docs/1028.html",
        PageClass.listing_index,
    ),
    "ogawa_house_page2.html": ("https://ogawamura.jp/house/page/2/", PageClass.listing_index),
    "ogawa_house_page1.html": ("https://ogawamura.jp/house/", PageClass.listing_index),
}


def _load(name: str) -> tuple[str, str]:
    url, _ = CASES[name]
    return decode_html((FIX / name).read_bytes(), None)[0], url


@pytest.mark.parametrize("name", sorted(CASES))
def test_regression_classification(name: str) -> None:
    html, url = _load(name)
    expected = CASES[name][1]
    got = classify_page(html, url, platforms=PlatformRegistry())
    assert got.page_class is expected, (name, got.page_class, got.signals)


@pytest.mark.parametrize(
    "name",
    ["ikusaka_tour.html", "obuse_subsidy.html", "sakaide_subsidy.html", "sakae_guide_834.html"],
)
def test_non_listing_pages_are_not_listing_like(name: str) -> None:
    html, url = _load(name)
    ls = listing_score(html, url)
    assert not ls.is_listing and ls.subsidy_dominant and ls.rows <= 1, ls.evidence


def test_listing_table_has_rows_and_columns() -> None:
    html, url = _load("sakae_list_1028.html")
    ls = listing_score(html, url)
    assert ls.is_listing and ls.rows >= 5 and ls.property_prices >= 5
    assert ls.has_price_col and ls.has_address_col
    assert not ls.pagination.is_paginated and ls.pagination.current_page == 1


def test_pagination_page2_points_to_first_page_and_is_mostly_closed() -> None:
    html2, url2 = _load("ogawa_house_page2.html")
    html1, url1 = _load("ogawa_house_page1.html")
    p2, p1 = listing_score(html2, url2), listing_score(html1, url1)
    assert p2.pagination.is_paginated and p2.pagination.current_page == 2
    assert p2.pagination.first_page_url == "https://ogawamura.jp/house/"
    assert p2.pagination.follow_pattern and re.search(
        p2.pagination.follow_pattern, "https://ogawamura.jp/house/page/3/"
    )
    assert p1.pagination.current_page == 1 and p1.pagination.first_page_url is None
    assert p1.pagination.follow_pattern == p2.pagination.follow_pattern
    # 2 ページ目は全件「ご成約済」、1 ページ目は募集中を含む
    assert p2.closed_hits > p1.closed_hits and p2.closed_hits >= 10
    assert p1.is_listing and p2.is_listing


def test_price_count_alone_does_not_make_a_listing() -> None:
    # 旧ロジックの「万円が 3 件以上なら一覧」を再現する補助金ページ
    html = (
        "<html><body><main><h1>空き家改修補助金</h1>"
        "<p>補助率は 2 分の 1、上限額 50万円。加算で最大 80万円。家財処分は上限 10万円。</p>"
        "<p>申請書（様式第1号）を提出してください。対象者と申請期間は要綱を参照。</p>"
        "</main></body></html>"
    )
    got = classify_page(html, "https://x.example/hojo.html", platforms=PlatformRegistry())
    assert got.page_class is PageClass.not_listing
    ls = listing_score(html, "https://x.example/hojo.html")
    assert ls.rows == 0 and ls.other_prices >= 3 and ls.property_prices == 0


def test_synthetic_listing_rows_are_counted() -> None:
    rows = "".join(
        f"<tr><td>物件 {i}</td><td>{i}00万円</td><td>木造 2階建 {80 + i}㎡</td><td>○○地区</td></tr>"
        for i in range(1, 6)
    )
    html = (
        "<html><body><table><tr><th>番号</th><th>価格</th><th>建物</th><th>所在地</th></tr>"
        f"{rows}</table><a href='/list/?page=2'>次へ</a></body></html>"
    )
    url = "https://x.example/list/"
    ls = listing_score(html, url)
    assert ls.is_listing and ls.rows == 5 and ls.has_price_col and ls.has_address_col
    assert ls.pagination.is_paginated and ls.pagination.current_page == 1
    assert re.search(ls.pagination.follow_pattern or "", "https://x.example/list/?page=2")
    assert (
        classify_page(html, url, platforms=PlatformRegistry()).page_class is PageClass.listing_index
    )


def test_detect_pagination_query_style() -> None:
    from selectolax.parser import HTMLParser

    info = detect_pagination(
        HTMLParser("<html><body></body></html>"), "https://x.example/l/?page=3"
    )
    assert info.is_paginated and info.current_page == 3
    assert info.first_page_url == "https://x.example/l/"
