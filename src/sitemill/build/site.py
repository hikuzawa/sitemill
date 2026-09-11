"""静的ビルド。Jinja2 で描画し、sitemap・robots・_redirects・_headers・検索索引を出す。"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
    select_autoescape,
)
from markupsafe import Markup

from sitemill.build import preflight
from sitemill.build.pii import PiiPolicy, allow_also, default_jp_gov_policy, scan_text
from sitemill.build.trust import verify_page_html
from sitemill.charts import Chart
from sitemill.embeds import render_embed
from sitemill.metrics.analytics import analytics_snippet
from sitemill.models import Embed, Page, utcnow
from sitemill.service import Service
from sitemill.settings import Workspace
from sitemill.store.jsonio import dumps

log = logging.getLogger(__name__)
# OS の時刻帯 DB が無い環境（Windows）があるため固定オフセットで表す（JST に夏時間は無い）
JST = timezone(timedelta(hours=9), "JST")


class BuildError(RuntimeError):
    """信頼シグナルの欠落など、公開してはいけない状態。"""


@dataclass
class BuildResult:
    pages: int = 0
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- Jinja フィルタ ---------------------------------------------------------


def yen(value: int | float | None) -> str:
    """金額の表示。1 万円以上は万円区切り、1 万円未満は円。

    サイト全体でこの 1 つに揃える（Jinja からは `|yen`）。40 万円を「400,000円」と
    「40万円」で書き分けると、同じ物件が別の額に見えるため。
    """
    if value is None:
        return "—"
    v = int(round(value))
    if v < 10_000:
        return f"{v:,}円"
    oku, rem = divmod(v, 100_000_000)
    man, tail = divmod(rem, 10_000)
    parts: list[str] = []
    if oku:
        parts.append(f"{oku:,}億")
    if man or tail:
        if tail:
            parts.append(f"{man + tail / 10_000:,.1f}万")
        else:
            parts.append(f"{man:,}万")
    return "".join(parts) + "円"


def m2(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.1f}㎡" if not float(value).is_integer() else f"{int(value):,}㎡"


def number(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,}"


def date_ja(value: datetime | None) -> str:
    if value is None:
        return "—"
    d = value.astimezone(JST) if value.tzinfo else value
    return f"{d.year}年{d.month}月{d.day}日"


def datetime_ja(value: datetime | None) -> str:
    if value is None:
        return "—"
    d = value.astimezone(JST) if value.tzinfo else value
    return f"{d.year}年{d.month}月{d.day}日 {d.hour:02d}:{d.minute:02d}"


def embed_filter(value: Embed) -> Markup:
    return Markup(render_embed(value))


def chart_filter(value: Chart) -> Markup:
    return Markup(value.html())


# --- ビルダー ---------------------------------------------------------------


class SiteBuilder:
    def __init__(self, ws: Workspace, service: Service, *, now: datetime | None = None) -> None:
        self.ws = ws
        self.service = service
        self.now = now or utcnow()
        loaders = [PackageLoader("sitemill", "templates")]
        if ws.templates_dir.is_dir():
            loaders.insert(0, FileSystemLoader(str(ws.templates_dir)))
        self.env = Environment(
            loader=ChoiceLoader(loaders),
            autoescape=select_autoescape(["html", "xml"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters.update(
            yen=yen,
            m2=m2,
            number=number,
            date_ja=date_ja,
            datetime_ja=datetime_ja,
            embed=embed_filter,
            chart=chart_filter,
        )
        analytics = analytics_snippet(ws.site.analytics.provider, ws.secrets.cf_web_analytics_token)
        self.env.globals.update(
            site=ws.site,
            base_url=ws.site.base_url,
            analytics=Markup(analytics),
            site_verification=ws.secrets.google_site_verification or "",
            build_time=self.now,
            url_for=ws.site.url,
        )
        maker = getattr(service, "pii_policy", None)
        base_policy: PiiPolicy = (maker(ws) if maker else None) or default_jp_gov_policy()
        # 運営者自身の連絡先は第三者の個人情報ではないので許可する。
        contact = (ws.site.operator.contact or "").strip()
        self.pii_policy = allow_also(base_policy, emails=[contact], phones=[contact])

    def render_page(self, page: Page) -> str:
        template = self.env.get_template(page.template)
        html = template.render(page=page, meta=page.meta, trust=page.trust, **page.context)
        problems = verify_page_html(html)
        problems += preflight.check_page_html(html, path=page.meta.path, noindex=page.meta.noindex)
        if problems:
            raise BuildError(f"{page.meta.path}: {'; '.join(problems)}")
        pii = scan_text(
            preflight.visible_text_and_contacts(html),
            policy=self.pii_policy,
            where=page.meta.path,
        )
        if pii:
            details = "; ".join(f.describe() for f in pii[:5])
            raise BuildError(f"個人情報らしき文字列がページに含まれる: {details}")
        return html

    def build(self) -> BuildResult:
        ws, result = self.ws, BuildResult()
        dist = ws.dist_dir
        if dist.exists():
            shutil.rmtree(dist)
        dist.mkdir(parents=True)

        pages = self.service.pages(ws, now=self.now)
        seen: set[str] = set()
        for page in pages:
            if page.meta.path in seen:
                raise BuildError(f"ページのパスが重複: {page.meta.path}")
            seen.add(page.meta.path)
            html = self.render_page(page)
            out = dist / page.meta.path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8", newline="\n")
            result.files.append(page.meta.path)
        result.pages = len(pages)

        if ws.static_dir.is_dir():
            shutil.copytree(ws.static_dir, dist / "static", dirs_exist_ok=True)
            result.files.append("static/")

        index: Any = self.service.search_index(ws)
        if index is not None:
            # 配列なら search/index.json に、辞書ならキーごとのファイルに書く。全国規模では
            # 索引が数 MB になるので、サービス側で分割できるようにしている
            parts = index if isinstance(index, dict) else {"index.json": index}
            for name, payload in parts.items():
                rel = f"search/{name}"
                path = dist / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(dumps(payload, indent=None), encoding="utf-8", newline="\n")
                result.files.append(rel)

        self._write(dist / "sitemap.xml", self._sitemap(pages))
        self._write(
            dist / "robots.txt",
            f"User-agent: *\nAllow: /\nSitemap: {ws.site.base_url}/sitemap.xml\n",
        )
        redirects = self.service.redirects(ws)
        self._write(
            dist / "_redirects",
            "".join(f"{r.from_path} {r.to_url} {r.status}\n" for r in redirects),
        )
        self._write(
            dist / "_headers",
            "/*\n  X-Content-Type-Options: nosniff\n"
            "  Referrer-Policy: strict-origin-when-cross-origin\n"
            "  X-Frame-Options: SAMEORIGIN\n",
        )
        result.files.extend(["sitemap.xml", "robots.txt", "_redirects", "_headers"])

        # Search Console 等の検証ファイルを置ける仕組み: verification/ の中身を dist 直下へ複写。
        vdir = ws.root / "verification"
        if vdir.is_dir():
            for f in sorted(vdir.iterdir()):
                if f.is_file() and f.name != "README.md" and not f.name.startswith("."):
                    shutil.copy2(f, dist / f.name)
                    result.files.append(f.name)
                    result.warnings.append(f"検証ファイルを配置: /{f.name}")

        problems = preflight.check_site(dist, analytics_token=ws.secrets.cf_web_analytics_token)
        if problems:
            raise BuildError("公開前チェックに失敗: " + "; ".join(problems))
        log.info("build: %d pages → %s", result.pages, dist)
        return result

    def _write(self, path: Any, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")

    def _sitemap(self, pages: list[Page]) -> str:
        base = self.ws.site.base_url
        rows = []
        for p in pages:
            if p.meta.noindex or not p.meta.path.endswith(".html"):
                continue
            lastmod = p.trust.updated_at.astimezone(JST).date().isoformat()
            rows.append(
                f"  <url><loc>{base}{p.meta.url_path}</loc><lastmod>{lastmod}</lastmod>"
                f"<changefreq>{p.meta.changefreq}</changefreq>"
                f"<priority>{p.meta.priority:.1f}</priority></url>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows)
            + "\n</urlset>\n"
        )
