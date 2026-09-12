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


def test_duplicate_urls_keep_every_label() -> None:
    """同じページに複数のラベルで張られているとき、どれも捨てない。

    栗林公園の実例: 案内ページには「開園日・開園時間」「入園料」「各種サービス(コインロッカー、
    車椅子)」が別々の断片リンクで張られている。1 つだけ選ぶ規則はどれも取りこぼす
    （最長を選ぶと「各種サービス…」が勝ち、探していた開園時間が消えた）。
    """
    from sitemill.fetch.links import extract_links

    html = """<html><body>
    <a href="/guide"><img src="/x.png" alt=""></a>
    <ul>
      <li><a href="/guide#c1">開園日・開園時間</a></li>
      <li><a href="/guide#c2">入園料</a></li>
      <li><a href="/guide#c3">各種サービス(コインロッカー、車椅子)</a></li>
    </ul>
    </body></html>"""
    links = extract_links(html, "https://example.jp/spot/")
    assert [lk.url for lk in links] == ["https://example.jp/guide"]
    assert links[0].labels == ("開園日・開園時間", "入園料", "各種サービス(コインロッカー、車椅子)")
    assert links[0].text == "開園日・開園時間"
    assert "開園日・開園時間" in links[0].labelled()


def test_duplicate_urls_keep_the_descriptive_label() -> None:
    """断片つきリンクを畳むとき、説明的なラベルを残す。

    栗林公園の実例: 同じ案内ページへ画像リンク（アンカー文字列が空）と
    「開園日・開園時間」「入園料」のリンクが並ぶ。先に来た画像リンクを残すと、
    目的のページを見つける手がかり（ラベル）が消えて「時間が取れない」ことになる。
    """
    from sitemill.fetch.links import extract_links

    html = """<html><body>
    <a href="/guide"><img src="/x.png" alt=""></a>
    <ul>
      <li><a href="/guide#contents1331">開園日・開園時間</a></li>
      <li><a href="/guide#contents1332">入園料</a></li>
    </ul>
    </body></html>"""
    links = extract_links(html, "https://example.jp/spot/")
    assert [lk.url for lk in links] == ["https://example.jp/guide"]
    assert links[0].text == "開園日・開園時間"
    assert links[0].labelled() == ("開園日・開園時間", "入園料")


def test_image_alt_is_used_when_the_anchor_has_no_text() -> None:
    from sitemill.fetch.links import extract_links

    html = '<a href="/guide"><img src="/x.png" alt="利用案内"></a>'
    links = extract_links(html, "https://example.jp/")
    assert links[0].text == "利用案内"
