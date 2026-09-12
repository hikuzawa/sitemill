"""多言語（ADR 0016）。ロケール定義・文言カタログ・ロケール別の書式。

サービスは site.toml の [[locales]] でロケールを宣言し、i18n/<code>.yaml に画面の文言を置く。
事実（時間・料金）は構造化データからロケール別に描画する。描画時に翻訳エンジンや LLM は使わない。
"""

from sitemill.i18n.catalog import Catalog, TranslationError, flatten, load_catalog, load_catalogs
from sitemill.i18n.format import (
    EN,
    FORMATS,
    JA,
    ZH_HANT,
    LocaleFormat,
    fmt_for,
    format_date,
    format_date_short,
    format_datetime,
    format_money,
    format_number,
    format_time,
    format_time_range,
    format_weekday,
    register_format,
)
from sitemill.i18n.locale import (
    DEFAULT_OG_LOCALES,
    LocaleConfig,
    base_language,
    og_locale_for,
)

__all__ = [
    "DEFAULT_OG_LOCALES",
    "EN",
    "FORMATS",
    "JA",
    "ZH_HANT",
    "Catalog",
    "LocaleConfig",
    "LocaleFormat",
    "TranslationError",
    "base_language",
    "flatten",
    "fmt_for",
    "format_date",
    "format_date_short",
    "format_datetime",
    "format_money",
    "format_number",
    "format_time",
    "format_time_range",
    "format_weekday",
    "load_catalog",
    "load_catalogs",
    "og_locale_for",
    "register_format",
]
