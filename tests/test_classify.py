"""ページ分類器のテスト。合成 HTML で各クラスを確認する（実データは akiya-atlas 側で検証）。"""

import pytest

from sitemill.classify import ClassifiedPage, PageClass, PlatformRegistry, classify_page

REG = PlatformRegistry()


def _classify(html: str, url: str = "https://city.example/akiya/") -> ClassifiedPage:
    return classify_page(html, url, platforms=REG)


def _card(i: int) -> str:
    return f"<div>[売買{300 + i * 50}万円]No.{200 + i}地名{i} コメント 続きを見る</div>"


INDEX_HTML = (
    "<html><body><main><h1>空き家バンク物件一覧</h1>"
    + "".join(_card(i) for i in range(8))
    + "</main></body></html>"
)

DETAIL_HTML = """<html><body><main><h1>[売買980万円]No.304 東御市鞍掛</h1>
<table>
<tr><th>所在地</th><td>東御市鞍掛</td></tr>
<tr><th>敷地面積</th><td>687.93m2</td></tr>
<tr><th>延床面積</th><td>160.82m2</td></tr>
<tr><th>価格</th><td>980万円</td></tr>
<tr><th>建築年</th><td>1974年</td></tr>
<tr><th>間取り</th><td>7DK</td></tr>
<tr><th>構造</th><td>木造2階建</td></tr>
</table></main></body></html>"""

SPA_HTML = (
    '<html><body><div id="app"></div>'
    "<noscript>We're sorry but this site doesn't work properly without JavaScript enabled. "
    "Please enable it to continue.</noscript>"
    '<script src="/js/chunk-vendors.abc.js"></script><script src="/js/app.def.js"></script>'
    '<script src="/js/chunk-1.ghi.js"></script></body></html>'
)

INFO_HTML = """<html><body><main><h1>空き家バンク制度について</h1>
<p>本市では空き家バンク制度を運営しています。利用登録の流れや申込方法をご案内します。</p>
<p>お問い合わせは移住交流推進課まで。</p></main></body></html>"""


def test_third_party_by_host() -> None:
    c = _classify("<html><body>x</body></html>", "https://tomi-c20219.akiya-athome.jp/")
    assert c.page_class is PageClass.third_party and c.confidence >= 0.9
    assert "akiya-athome.jp" in c.signals.get("platform_host", "")


def test_spa_detection() -> None:
    c = _classify(SPA_HTML, "https://www.ina-akiyabank.jp/")
    assert c.page_class is PageClass.spa
    assert c.signals["scripts"] >= 3 and c.signals["spa_markers"] >= 1


def test_listing_index() -> None:
    c = _classify(INDEX_HTML)
    assert c.page_class is PageClass.listing_index
    assert c.confidence >= 0.6 and c.signals["price_count"] >= 6


def test_listing_detail() -> None:
    c = _classify(DETAIL_HTML)
    assert c.page_class is PageClass.listing_detail
    assert c.signals["field_count"] >= 5


def test_not_listing() -> None:
    c = _classify(INFO_HTML)
    assert c.page_class is PageClass.not_listing


def test_registry_extensible() -> None:
    reg = PlatformRegistry({"example-portal.jp"})
    assert reg.is_platform("https://sub.example-portal.jp/x")
    assert not reg.is_platform("https://city.example.lg.jp/")
    reg.add(["another.jp"])
    assert reg.is_platform("https://another.jp/")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (INDEX_HTML, PageClass.listing_index),
        (DETAIL_HTML, PageClass.listing_detail),
        (SPA_HTML, PageClass.spa),
        (INFO_HTML, PageClass.not_listing),
    ],
)
def test_confidence_in_range(html: str, expected: PageClass) -> None:
    c = _classify(html)
    assert c.page_class is expected
    assert 0.05 <= c.confidence <= 0.99
