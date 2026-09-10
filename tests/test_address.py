import pytest

from sitemill.parse.jp.address import has_street_number, strip_street_number


@pytest.mark.parametrize(
    ("text", "kept", "removed"),
    [
        ("東御市鞍掛593-1", "東御市鞍掛", "593-1"),
        ("東御市滋野乙4000-1", "東御市滋野", "乙4000-1"),
        ("東御市滋野乙４０００−１", "東御市滋野", "乙4000-1"),
        ("飯山市大字飯山", "飯山市大字飯山", None),
        ("長野県佐久市内山", "長野県佐久市内山", None),
        ("佐久市中込3丁目12-5 ハイツ101", "佐久市中込3丁目", "12-5 ハイツ101"),
        ("松本市大手一丁目1番1号", "松本市大手一丁目", "1番1号"),
        ("○○市二番町", "○○市二番町", None),
        ("六日町123", "六日町", "123"),
        ("三本柳", "三本柳", None),
        ("東御市 県281-2", "東御市 県", "281-2"),
        ("大町市大町字上仲町2500-1", "大町市大町字上仲町", "2500-1"),  # 字（小字）は地名なので残す
        ("白鳥台", "白鳥台", None),
        ("123-4", "", "123-4"),
        ("", "", None),
    ],
)
def test_strip_street_number(text: str, kept: str, removed: str | None) -> None:
    assert strip_street_number(text) == (kept, removed)


def test_has_street_number() -> None:
    assert has_street_number("東御市鞍掛593-1")
    assert has_street_number("松本市大手一丁目1番1号")
    assert not has_street_number("松本市大手一丁目")
    assert not has_street_number("飯山市大字飯山")
    assert not has_street_number("六日町")
