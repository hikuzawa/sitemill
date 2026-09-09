from datetime import date

import pytest

from sitemill.parse.jp import parse_area_m2, parse_date, parse_int, parse_year, parse_yen


@pytest.mark.parametrize(
    ("text", "expected", "note"),
    [
        ("1,200万円", 12_000_000, None),
        ("１，２００万円", 12_000_000, None),
        ("980万円（税込）", 9_800_000, None),
        ("1億2,000万円", 120_000_000, None),
        ("6.8万円", 68_000, None),
        ("68,000円/月", 68_000, None),
        ("月額 5万円", 50_000, None),
        ("2万5千円", 25_000, None),
        ("300万", 3_000_000, None),
        ("5,000,000円", 5_000_000, None),
        ("価格：1,000万円（相談可）", 10_000_000, None),
        ("価格 応相談", None, "negotiable"),
        ("300万円〜500万円", None, "range"),
        ("300万円～500万円", None, "range"),
        ("売買 500万円 / 賃貸 5万円", None, "multiple"),
        ("3DK", None, "no_number"),
        ("土地 300㎡", None, "no_number"),
        ("築1975年 価格500万円", 5_000_000, None),
        ("No.1234", None, "no_number"),
        ("", None, "no_number"),
    ],
)
def test_parse_yen(text: str, expected: int | None, note: str | None) -> None:
    assert parse_yen(text) == (expected, note)


@pytest.mark.parametrize(
    ("text", "expected", "note"),
    [
        ("120.5㎡", 120.5, None),
        ("120.5m2", 120.5, None),
        ("120.5m²", 120.5, None),
        ("延床面積 98.5平米", 98.5, None),
        ("1,234.56㎡", 1234.56, None),
        ("36.4坪", 120.33, "from_tsubo"),
        ("120.5㎡（36.4坪）", 120.5, None),
        ("土地 300㎡ 建物 120㎡", None, "multiple"),
        ("100〜200㎡", None, "range"),
        ("面積不明", None, "no_number"),
        ("3LDK", None, "no_unit"),
    ],
)
def test_parse_area(text: str, expected: float | None, note: str | None) -> None:
    assert parse_area_m2(text) == (expected, note)


@pytest.mark.parametrize(
    ("text", "expected", "note"),
    [
        ("昭和45年", 1970, None),
        ("昭和45年築", 1970, None),
        ("平成3年3月", 1991, None),
        ("令和元年", 2019, None),
        ("令和2年", 2020, None),
        ("S45年", 1970, None),
        ("H3.4", 1991, None),
        ("1975年", 1975, None),
        ("1975年築", 1975, None),
        ("1985年頃", 1985, "approx"),
        ("築約40年", None, "relative_age"),
        ("築40年", None, "relative_age"),
        ("築年不詳", None, "unknown"),
        ("2LDK", None, "no_year"),
        ("価格 500万円", None, "no_year"),
        ("１９７５年", 1975, None),
    ],
)
def test_parse_year(text: str, expected: int | None, note: str | None) -> None:
    assert parse_year(text) == (expected, note)


@pytest.mark.parametrize(
    ("text", "expected", "note"),
    [
        ("令和8年3月31日", date(2026, 3, 31), None),
        ("2026年3月31日", date(2026, 3, 31), None),
        ("2026/03/31", date(2026, 3, 31), None),
        ("2026-3-31", date(2026, 3, 31), None),
        ("締切 令和8年2月30日", None, "invalid_date"),
        ("随時", None, "no_date"),
    ],
)
def test_parse_date(text: str, expected: date | None, note: str | None) -> None:
    assert parse_date(text) == (expected, note)


def test_parse_int() -> None:
    assert parse_int("物件番号 ３２９") == 329
    assert parse_int("12.5") is None
    assert parse_int("なし") is None
