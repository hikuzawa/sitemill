from sitemill.diff import normalize_for_hash, page_text, select_main, squash

HTML = """<html><head><title>t</title><script>var x=1;</script><style>p{}</style></head>
<body><header>ヘッダ</header><nav><a href="/">home</a></nav>
<main id="content"><h1>空き家バンク物件一覧</h1>
<table><tr><th>物件番号</th><th>価格</th></tr><tr><td>A-1</td><td>500<b>万円</b></td></tr></table>
<p>所在地：東御市本海野<br>木造2階建</p><div class="counter">アクセス数: 1234</div></main>
<footer>フッタ</footer></body></html>"""


def test_page_text_keeps_structure_and_drops_noise() -> None:
    text = page_text(HTML)
    assert "ヘッダ" not in text
    assert "フッタ" not in text
    assert "var x" not in text
    assert "物件番号 | 価格" in text
    assert "A-1 | 500万円" in text
    assert "所在地:東御市本海野\n木造2階建" in text


def test_hash_ignores_noise_and_patterns() -> None:
    ignore = [r"アクセス数:\s*\d+"]
    assert normalize_for_hash(HTML, ignore_patterns=ignore) == normalize_for_hash(
        HTML.replace("1234", "1235"), ignore_patterns=ignore
    )
    assert normalize_for_hash(HTML) != normalize_for_hash(HTML.replace("1234", "1235"))
    assert normalize_for_hash(HTML) == normalize_for_hash(HTML.replace("フッタ", "別のフッタ"))


def test_select_main_prefers_selector_then_known_ids_then_body() -> None:
    assert select_main(HTML, "#content").attributes.get("id") == "content"
    assert select_main(HTML).attributes.get("id") == "content"
    assert select_main("<html><body><p>x</p></body></html>").tag == "body"
    tiny_main = "<html><body><main>a</main><div>" + "本文" * 200 + "</div></body></html>"
    assert select_main(tiny_main).tag == "body"


def test_squash() -> None:
    assert squash("価格 ５００万円\n") == "価格500万円"


def test_page_wrapped_in_one_form_keeps_its_body() -> None:
    """ASP.NET 系 CMS はページ全体を form で囲む。捨てると本文が消える（全国展開で発見）。"""
    from sitemill.diff.normalize import page_text

    # script が本文より長いことがある。form の割合は script を落とした後で測る
    html = (
        "<html><body><script>" + ("var x=1;" * 400) + "</script>"
        "<form id='aspnetForm'><h1>空き家バンク物件一覧</h1>"
        "<table><tr><td>No.1</td><td>350万円</td><td>木造 80㎡</td></tr>"
        "<tr><td>No.2</td><td>500万円</td><td>木造 95㎡</td></tr></table>"
        "</form></body></html>"
    )
    text = page_text(html)
    assert "空き家バンク物件一覧" in text and "350万円" in text


def test_small_search_form_is_still_dropped() -> None:
    from sitemill.diff.normalize import page_text

    body = "<p>" + "本文の説明。" * 40 + "</p>"
    html = f"<html><body><form><input name='q'>検索</form>{body}</body></html>"
    text = page_text(html)
    assert "検索" not in text and "本文の説明" in text
