"""ロケールの定義。site.toml の [[locales]] から読む（ADR 0016）。"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# og:locale は言語コードから機械的には決まらないので表で持つ。
# 足りなければ site.toml の [[locales]] で個別に指定する。
DEFAULT_OG_LOCALES: dict[str, str] = {
    "ja": "ja_JP",
    "en": "en_US",
    "zh-Hant": "zh_TW",
    "zh-Hans": "zh_CN",
    "ko": "ko_KR",
    "th": "th_TH",
    "vi": "vi_VN",
    "id": "id_ID",
    "fr": "fr_FR",
    "de": "de_DE",
    "es": "es_ES",
    "it": "it_IT",
    "pt": "pt_PT",
    "ru": "ru_RU",
}


def og_locale_for(code: str) -> str:
    """言語コードを OGP の og:locale 表記にする。表に無ければ区切りだけを置き換える。"""
    return DEFAULT_OG_LOCALES.get(code) or code.replace("-", "_")


def base_language(code: str) -> str:
    """ "zh-Hant" → "zh"。書式の既定を引くときに使う。"""
    return code.split("-", 1)[0]


class LocaleConfig(BaseModel):
    """1 言語分の設定。URL の接頭辞・html lang・og:locale・言語切り替えの表示名。"""

    code: str  # BCP 47。例: ja / en / zh-Hant
    path: str = ""  # URL の接頭辞。既定ロケールは空（ルートに置く）
    label: str = ""  # 言語切り替えの表示。その言語自身の表記で書く（例: 繁體中文）
    html_lang: str | None = None  # 既定は code
    og_locale: str | None = None  # 既定は DEFAULT_OG_LOCALES
    default: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("locale の code は空にできない")
        return v

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = v.strip().strip("/")
        if v and not v.replace("-", "").isalnum():
            raise ValueError(f"locale の path は英数字とハイフンだけにする: {v!r}")
        return v.lower()

    @property
    def lang(self) -> str:
        """<html lang> と hreflang に使う値。"""
        return self.html_lang or self.code

    @property
    def og(self) -> str:
        return self.og_locale or og_locale_for(self.code)

    @property
    def display(self) -> str:
        return self.label or self.code

    def dist_path(self, path: str) -> str:
        """dist 内の相対パスに接頭辞を付ける。例: ("about/index.html") → "en/about/index.html"。"""
        rel = path.replace("\\", "/").lstrip("/")
        return f"{self.path}/{rel}" if self.path else rel

    def url_path(self, path: str) -> str:
        """公開 URL のパスに接頭辞を付ける。例: ("/about/") → "/en/about/"。"""
        rel = path.replace("\\", "/").lstrip("/")
        return f"/{self.path}/{rel}" if self.path else f"/{rel}"
