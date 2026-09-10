"""リンク行の抽出（アンカー文字列・alt・行のテキスト）のテスト。ネットワーク不要。"""

from sitemill.fetch.links import extract_link_rows

TABLE = (
    """
<html><body>
<table>
  <tr><th>市町名</th><th>ホームページ</th></tr>
  <tr><td>輪島市</td><td><a href="https://www.city.wajima.ishikawa.jp/">https://www.city.wajima.ishikawa.jp/</a></td></tr>
  <tr><td>宝達志水町</td><td><a href="https://www.hodatsushimizu.jp/">https://www.hodatsushimizu.jp/</a></td></tr>
</table>
<ul>
  <li><a href="https://www.city.kanazawa.lg.jp/"><img src="/img/kanazawa.png" alt="金沢市"></a></li>
  <li><a href="https://www.town.tsubata.lg.jp/" title="津幡町"></a></li>
</ul>
<div><a href="https://example.com/a">A</a><a href="https://example.com/b">B</a>
"""
    + ("長い説明。" * 40)
    + """</div>
</body></html>
"""
)


def test_row_context_carries_the_name_next_to_the_link() -> None:
    rows = {r.url: r for r in extract_link_rows(TABLE, "https://www.pref.ishikawa.lg.jp/x.html")}
    wajima = rows["https://www.city.wajima.ishikawa.jp/"]
    # アンカー文字列は URL そのものでも、行（tr）に市町名が入っている
    assert wajima.text.startswith("https://")
    assert "輪島市" in wajima.context
    assert "宝達志水町" in rows["https://www.hodatsushimizu.jp/"].context


def test_alt_and_title_stand_in_for_empty_anchor_text() -> None:
    rows = {r.url: r for r in extract_link_rows(TABLE, "https://www.pref.ishikawa.lg.jp/x.html")}
    assert rows["https://www.city.kanazawa.lg.jp/"].text == "金沢市"
    assert rows["https://www.town.tsubata.lg.jp/"].text == "津幡町"


def test_long_container_is_not_used_as_context() -> None:
    rows = {r.url: r for r in extract_link_rows(TABLE, "https://www.pref.ishikawa.lg.jp/x.html")}
    assert rows["https://example.com/a"].context == ""  # 長すぎる div は行として採らない


def test_nearest_preceding_heading_is_recorded() -> None:
    # 石川県の実ページ: 見出しが市町名、リンクは「ホームページ：<URL>」の段落にある
    html = """
    <div class="city_heading"><h3>輪島市（わじまし）</h3></div>
    <div class="city_block_address"><p>住所：輪島市二ツ屋2字29</p>
      <p>ホームページ：<a href="https://www.city.wajima.ishikawa.jp/">https://www.city.wajima.ishikawa.jp/</a></p></div>
    <div class="city_heading"><h3>珠洲市（すずし）</h3></div>
    <div class="city_block_address"><p>ホームページ：<a href="https://www.city.suzu.lg.jp/">https://www.city.suzu.lg.jp/</a></p></div>
    """
    rows = {r.url: r for r in extract_link_rows(html, "https://www.pref.ishikawa.lg.jp/x.html")}
    assert rows["https://www.city.wajima.ishikawa.jp/"].heading.startswith("輪島市")
    assert rows["https://www.city.suzu.lg.jp/"].heading.startswith("珠洲市")


def test_base_href_and_relative_urls() -> None:
    html = '<base href="https://x.example/sub/"><a href="a.html">A</a>'
    rows = extract_link_rows(html, "https://y.example/")
    assert rows[0].url == "https://x.example/sub/a.html"
