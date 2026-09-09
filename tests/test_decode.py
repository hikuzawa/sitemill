from sitemill.fetch.decode import canonical_encoding, decode_html

LONG_JA = (
    "長野県東御市の空き家バンクでは、市内にある空き家住宅の売買・賃貸物件を紹介しています。"
    "物件の詳細や内見をご希望の方は、まず利用登録をお願いします。登録後に所在地などの詳細情報をお送りします。"
)


def test_canonical_encoding_aliases() -> None:
    assert canonical_encoding("Shift_JIS") == "cp932"
    assert canonical_encoding("utf8") == "utf-8"
    assert canonical_encoding("EUC-JP") == "euc_jp"
    assert canonical_encoding("bogus-enc") is None
    assert canonical_encoding(None) is None


def test_meta_charset_sjis() -> None:
    html = f'<html><head><meta charset="Shift_JIS"></head><body>{LONG_JA}</body></html>'
    text, enc = decode_html(html.encode("cp932"), "text/html")
    assert enc == "cp932"
    assert "空き家バンク" in text


def test_header_charset_used_when_no_meta() -> None:
    data = f"<html><body>{LONG_JA}</body></html>".encode("euc_jp")
    text, enc = decode_html(data, "text/html; charset=EUC-JP")
    assert enc == "euc_jp"
    assert "空き家" in text


def test_bom_utf8() -> None:
    data = b"\xef\xbb\xbf" + "<p>空き家</p>".encode()
    text, enc = decode_html(data, None)
    assert enc == "utf-8"
    assert text.startswith("<p>")


def test_wrong_meta_and_weak_header_fall_back_to_detection() -> None:
    data = f"<meta charset=utf-8><p>{LONG_JA}</p>".encode("cp932")
    text, _enc = decode_html(data, "text/html; charset=ISO-8859-1")
    assert "空き家バンク" in text
