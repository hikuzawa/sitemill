"""設定の読み込み。公開設定は site.toml、秘密情報は .env と環境変数だけから読む。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sitemill import __version__
from sitemill.i18n.locale import LocaleConfig

SITE_FILE = "site.toml"


class SecretsError(RuntimeError):
    """必要な秘密情報が無いときに投げる。メッセージに .env へ書く変数名を含める。"""


class Secrets(BaseSettings):
    """秘密情報。.env（サービスのルート）と環境変数だけを見る。他の場所は探索しない。"""

    model_config = SettingsConfigDict(
        extra="ignore", env_file_encoding="utf-8", case_sensitive=False
    )

    anthropic_api_key: str | None = None
    google_maps_embed_key: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None
    cf_web_analytics_token: str | None = None
    google_site_verification: str | None = None
    sitemill_llm_provider: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return None
            if v.startswith("op://"):
                raise ValueError(
                    f"未解決の op:// 参照です（{v[:32]}…）。.env には値そのものを書いてください"
                )
        return v

    @classmethod
    def load(cls, root: Path) -> Secrets:
        return cls(_env_file=root / ".env")

    def require(self, name: str) -> str:
        value = getattr(self, name.lower(), None)
        if not value:
            raise SecretsError(
                f"{name.upper()} が設定されていません。サービスのルートにある .env に"
                f" `{name.upper()}=<値>` を書いてください（.env.example 参照）"
            )
        return value


class OperatorConfig(BaseModel):
    name: str = "準備中"
    contact: str = "準備中"
    url: str | None = None


class PathsConfig(BaseModel):
    data: str = "data"
    templates: str = "templates"
    static: str = "static"
    dist: str = "dist"
    fixtures: str = "tests/fixtures"
    i18n: str = "i18n"  # 文言カタログ <locale>.yaml の置き場（無くてもよい）


class CrawlConfig(BaseModel):
    default_delay_seconds: float = 3.0
    jitter_seconds: float = 1.0
    timeout_seconds: float = 30.0
    max_pages_per_source: int = 30
    user_agent: str | None = None
    # ホスト単位の並列数（ADR 0013）。1 ホストあたりの間隔は並列でも縮まらない
    max_workers: int = 4
    # 変化の少ないサイトは巡回間隔を延ばす（相手サイトへの負荷を下げる）。
    # 下限は max_interval_days（既定 7 日）で、必ず週 1 回は取りに行く
    adaptive_interval: bool = True
    fresh_days: int = 7  # この日数内に変化があれば毎日
    slow_after_days: int = 28  # これ以上変化が無ければ最長間隔
    mid_interval_days: int = 3  # その中間
    max_interval_days: int = 7  # 最長（＝週 1 回）


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5"
    max_input_chars: int = 60_000
    max_output_tokens: int = 16_000
    temperature: float | None = 0.0


class AnalyticsConfig(BaseModel):
    provider: str = "cloudflare"


class SiteConfig(BaseModel):
    id: str
    name: str
    base_url: str
    service: str
    language: str = "ja"
    description: str = ""
    # 多言語のときだけ書く（ADR 0016）。空なら language の 1 ロケールとして扱う
    locales: list[LocaleConfig] = Field(default_factory=list)
    operator: OperatorConfig = Field(default_factory=OperatorConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url は http(s):// で始める")
        return v

    @model_validator(mode="after")
    def _check_locales(self) -> SiteConfig:
        if not self.locales:
            return self
        codes = [lc.code for lc in self.locales]
        if len(codes) != len(set(codes)):
            raise ValueError("locales の code が重複している")
        paths = [lc.path for lc in self.locales]
        if len(paths) != len(set(paths)):
            raise ValueError("locales の path が重複している")
        if sum(1 for lc in self.locales if lc.default) > 1:
            raise ValueError("既定ロケール（default = true）は 1 つだけにする")
        if self.language not in codes:
            raise ValueError(f"language（{self.language}）を locales のどれかに一致させる")
        if self.default_locale.path:
            raise ValueError(
                f"既定ロケール（{self.default_locale.code}）の path は空にする"
                "（ルートに置き、hreflang の x-default が指す先にする）"
            )
        return self

    @property
    def locale_list(self) -> list[LocaleConfig]:
        """宣言されたロケール。単一言語のサービスでは language の 1 件として見せる。"""
        return self.locales or [LocaleConfig(code=self.language, default=True)]

    @property
    def default_locale(self) -> LocaleConfig:
        locales = self.locale_list
        return next((lc for lc in locales if lc.default), locales[0])

    @property
    def multilingual(self) -> bool:
        return len(self.locale_list) > 1

    def locale(self, code: str | None) -> LocaleConfig:
        """コードからロケールを引く。None なら既定ロケール。"""
        if code is None:
            return self.default_locale
        for lc in self.locale_list:
            if lc.code == code:
                return lc
        raise KeyError(f"site.toml の [[locales]] に無いロケール: {code}")

    @property
    def user_agent(self) -> str:
        default = f"sitemill/{__version__} ({self.id}; +{self.base_url}/about/)"
        return self.crawl.user_agent or default

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @classmethod
    def load(cls, path: Path) -> SiteConfig:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        site = dict(data.get("site", {}))
        for key in ("operator", "paths", "crawl", "llm", "analytics", "locales"):
            if key in data:
                site[key] = data[key]
        return cls.model_validate(site)


def find_root(start: Path | None = None) -> Path:
    """site.toml を持つディレクトリを、start から親方向に探す。"""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / SITE_FILE).is_file():
            return candidate
    raise FileNotFoundError(f"{SITE_FILE} が見つかりません（{here} から上位を探索）")


@dataclass(frozen=True)
class Workspace:
    """サービスのルートと、設定から導いた各ディレクトリ。"""

    root: Path
    site: SiteConfig
    secrets: Secrets

    @classmethod
    def open(cls, root: Path | None = None) -> Workspace:
        root = find_root(root)
        return cls(root=root, site=SiteConfig.load(root / SITE_FILE), secrets=Secrets.load(root))

    @property
    def data_dir(self) -> Path:
        return self.root / self.site.paths.data

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def records_dir(self) -> Path:
        return self.data_dir / "records"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def llm_cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"

    @property
    def templates_dir(self) -> Path:
        return self.root / self.site.paths.templates

    @property
    def i18n_dir(self) -> Path:
        """文言カタログの置き場。無くてもよい（単一言語のサービス）。"""
        return self.root / self.site.paths.i18n

    @property
    def static_dir(self) -> Path:
        return self.root / self.site.paths.static

    @property
    def dist_dir(self) -> Path:
        return self.root / self.site.paths.dist

    @property
    def fixtures_dir(self) -> Path:
        return self.root / self.site.paths.fixtures

    def ensure_dirs(self) -> None:
        for d in (
            self.raw_dir,
            self.state_dir,
            self.records_dir,
            self.runs_dir,
            self.llm_cache_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
