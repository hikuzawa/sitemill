"""多言語の部品（ADR 0016）。ロケール定義・書式・文言カタログ。"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from sitemill.i18n import (
    Catalog,
    LocaleConfig,
    TranslationError,
    flatten,
    format_date,
    format_date_short,
    format_datetime,
    format_money,
    format_number,
    format_time,
    format_time_range,
    format_weekday,
    load_catalogs,
    og_locale_for,
)
from sitemill.settings import SiteConfig

D = date(2026, 9, 12)  # 土曜


def test_locale_paths() -> None:
    ja = LocaleConfig(code="ja", default=True)
    en = LocaleConfig(code="en", path="en")
    assert ja.dist_path("about/index.html") == "about/index.html"
    assert en.dist_path("about/index.html") == "en/about/index.html"
    assert ja.url_path("/about/") == "/about/"
    assert en.url_path("/about/") == "/en/about/"


def test_locale_normalizes_path_and_derives_lang() -> None:
    zh = LocaleConfig(code="zh-Hant", path="/zh-Hant/")
    assert zh.path == "zh-hant"  # URL は小文字に寄せる
    assert zh.lang == "zh-Hant"  # hreflang は BCP 47 のまま
    assert zh.og == "zh_TW"


def test_locale_rejects_odd_path() -> None:
    with pytest.raises(ValidationError):
        LocaleConfig(code="en", path="en/us")


def test_og_locale_falls_back_to_the_code() -> None:
    assert og_locale_for("ja") == "ja_JP"
    assert og_locale_for("sv-SE") == "sv_SE"


def test_date_formats_differ_by_locale() -> None:
    assert format_date(D, "ja") == "2026年9月12日"
    assert format_date(D, "zh-Hant") == "2026年9月12日"
    assert format_date(D, "en") == "12 September 2026"


def test_short_date_carries_the_weekday() -> None:
    """「今日開いているか」の帯は曜日が読めないと使えない（ADR 0004）。"""
    assert format_date_short(D, "ja") == "9月12日（土）"
    assert format_date_short(D, "zh-Hant") == "9月12日（週六）"
    assert format_date_short(D, "en") == "Sat, 12 Sep"
    assert format_weekday(D, "ja") == "土"


def test_unknown_locale_falls_back_to_the_base_language_then_english() -> None:
    assert format_date(D, "zh-Hant-HK") == "2026年9月12日"  # zh に落ちる
    assert format_date(D, "fi") == "12 September 2026"  # 英語に落ちる
    assert format_date(D, None) == "12 September 2026"


def test_time_is_24h_in_every_locale() -> None:
    """12 時間表記への変換は「17:00 を 5:00 と書く」取り違えを生むので行わない。"""
    assert format_time(time(17, 0), "en") == "17:00"
    assert format_time(time(9, 5), "ja") == "09:05"
    assert format_time_range(time(10, 0), time(17, 0), "ja") == "10:00–17:00"
    assert format_time_range(time(10, 0), None, "ja") == "10:00"


def test_datetime_and_numbers() -> None:
    assert format_datetime(datetime(2026, 9, 12, 6, 10), "ja") == "2026年9月12日 06:10"
    assert format_number(1234567, "ja") == "1,234,567"
    assert format_number(12.25, "en") == "12.2"


def test_money_puts_the_unit_where_the_locale_expects_it() -> None:
    assert format_money(2100, "ja") == "2,100円"
    assert format_money(2100, "en") == "¥2,100"
    assert format_money(2100, "zh-Hant") == "2,100日圓"


def test_flatten_nests_keys_with_dots() -> None:
    assert flatten({"nav": {"today": "今日"}, "title": "見出し"}) == {
        "nav.today": "今日",
        "title": "見出し",
    }


def test_catalog_substitutes_params() -> None:
    cat = Catalog(locale="ja", entries={"trust.count_value": "{count} 件"})
    assert cat.get("trust.count_value", count="120") == "120 件"


def test_catalog_falls_back_and_records_the_miss() -> None:
    base = Catalog(locale="ja", entries={"a": "あ", "b": "い"})
    en = Catalog(locale="en", entries={"a": "A"}, fallback=base)
    assert en.get("a") == "A"
    assert en.get("b") == "い"  # 未翻訳は既定ロケールの文言を出す（公開は止めない）
    assert en.misses == {"b"}
    assert en.has("b") and not en.has("zzz")


def test_catalog_raises_when_the_key_is_nowhere() -> None:
    """テンプレートの書き間違いは黙って空文字にせず止める。"""
    cat = Catalog(locale="ja", entries={})
    with pytest.raises(TranslationError):
        cat.get("nope")


def test_load_catalogs_reads_files_and_tolerates_missing_ones(tmp_path: Path) -> None:
    (tmp_path / "ja.yaml").write_text("nav:\n  today: 今日\n", encoding="utf-8")
    (tmp_path / "en.yaml").write_text("nav:\n  today: Today\n", encoding="utf-8")
    cats = load_catalogs(tmp_path, ["ja", "en", "zh-Hant"], default_code="ja")
    assert cats["en"].get("nav.today") == "Today"
    assert cats["zh-Hant"].get("nav.today") == "今日"  # ファイルが無くても既定に落ちる


def _site(locales: str) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "id": "x",
            "name": "X",
            "base_url": "https://x.example",
            "service": "m:s",
            "language": "ja",
            **({"locales": locales} if locales else {}),  # type: ignore[dict-item]
        }
    )


THREE = [
    {"code": "ja", "path": "", "default": True},
    {"code": "en", "path": "en"},
    {"code": "zh-Hant", "path": "zh-hant"},
]


def test_single_language_site_still_has_one_locale() -> None:
    site = _site("")
    assert not site.multilingual
    assert site.default_locale.code == "ja"
    assert site.locale(None).code == "ja"


def test_multilingual_site_config() -> None:
    site = _site(THREE)  # type: ignore[arg-type]
    assert site.multilingual
    assert [lc.code for lc in site.locale_list] == ["ja", "en", "zh-Hant"]
    assert site.default_locale.code == "ja"
    assert site.locale("en").path == "en"
    with pytest.raises(KeyError):
        site.locale("ko")


def test_default_locale_must_sit_at_the_root() -> None:
    """既定ロケールがルート以外にあると hreflang の x-default が指す先が決まらない。"""
    with pytest.raises(ValidationError, match="path は空"):
        _site([{"code": "ja", "path": "ja", "default": True}, {"code": "en", "path": "en"}])  # type: ignore[arg-type]


def test_language_must_be_one_of_the_locales() -> None:
    with pytest.raises(ValidationError, match="language"):
        SiteConfig.model_validate(
            {
                "id": "x",
                "name": "X",
                "base_url": "https://x.example",
                "service": "m:s",
                "language": "ko",
                "locales": THREE,
            }
        )


def test_duplicate_locales_are_rejected() -> None:
    with pytest.raises(ValidationError, match="重複"):
        _site([{"code": "ja", "default": True}, {"code": "ja", "path": "ja2"}])  # type: ignore[arg-type]
