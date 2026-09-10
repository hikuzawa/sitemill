"""設定の読み込み。公開設定は site.toml、秘密情報は .env と環境変数だけから読む。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sitemill import __version__

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


class CrawlConfig(BaseModel):
    default_delay_seconds: float = 3.0
    jitter_seconds: float = 1.0
    timeout_seconds: float = 30.0
    max_pages_per_source: int = 30
    user_agent: str | None = None
    # ホスト単位の並列数（ADR 0013）。1 ホストあたりの間隔は並列でも縮まらない
    max_workers: int = 4


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
        for key in ("operator", "paths", "crawl", "llm", "analytics"):
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
