"""静的ビルド。Jinja2 で描画し、sitemap・robots・_redirects・_headers・検索索引を出す。"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
    pass_context,
    select_autoescape,
)
from markupsafe import Markup

from sitemill.build import preflight
from sitemill.build.pii import PiiPolicy, allow_also, default_jp_gov_policy, scan_text
from sitemill.build.trust import verify_page_html
from sitemill.charts import Chart
from sitemill.embeds import render_embed
from sitemill.i18n import (
    Catalog,
    LocaleConfig,
    format_date,
    format_date_short,
    format_datetime,
    format_money,
    format_number,
    format_time,
    format_weekday,
    load_catalogs,
    og_locale_for,
)
from sitemill.metrics.analytics import analytics_snippet
from sitemill.models import Embed, Page, PageMeta, utcnow
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


# --- ロケール別のフィルタ（ADR 0016）--------------------------------------
# 描画中のページのロケールは、ビルダーが render 変数 `locale` として渡す。
# 単一言語のサービスは既存の `date_ja` などをそのまま使えばよい（挙動は変えていない）。


def _ctx_code(ctx: Any) -> str | None:
    locale = ctx.get("locale")
    return locale.code if isinstance(locale, LocaleConfig) else None


def _code_of(locale: LocaleConfig | str | None) -> str | None:
    """LocaleConfig でも文字列でも受ける。マクロから明示的に渡してもらうときに使う。"""
    if isinstance(locale, LocaleConfig):
        return locale.code
    return locale or None


def _as_jst(value: datetime) -> datetime:
    return value.astimezone(JST) if value.tzinfo else value


@pass_context
def date_l(ctx: Any, value: date | datetime | None) -> str:
    if value is None:
        return "—"
    d = _as_jst(value).date() if isinstance(value, datetime) else value
    return format_date(d, _ctx_code(ctx))


@pass_context
def date_short_l(ctx: Any, value: date | datetime | None) -> str:
    """曜日つきの短い日付。「9月12日（金）」「Fri, 12 Sep」。"""
    if value is None:
        return "—"
    d = _as_jst(value).date() if isinstance(value, datetime) else value
    return format_date_short(d, _ctx_code(ctx))


@pass_context
def weekday_l(ctx: Any, value: date | datetime | None) -> str:
    if value is None:
        return "—"
    d = _as_jst(value).date() if isinstance(value, datetime) else value
    return format_weekday(d, _ctx_code(ctx))


@pass_context
def datetime_l(ctx: Any, value: datetime | None) -> str:
    if value is None:
        return "—"
    return format_datetime(_as_jst(value), _ctx_code(ctx))


@pass_context
def time_l(ctx: Any, value: time | datetime | None) -> str:
    if value is None:
        return "—"
    t = _as_jst(value).time() if isinstance(value, datetime) else value
    return format_time(t, _ctx_code(ctx))


@pass_context
def number_l(ctx: Any, value: int | float | None) -> str:
    return "—" if value is None else format_number(value, _ctx_code(ctx))


@pass_context
def money_l(ctx: Any, value: int | None) -> str:
    """日本円の金額をロケールの表記で出す（2,100円 / ¥2,100 / 2,100日圓）。"""
    return "—" if value is None else format_money(int(value), _ctx_code(ctx))


# ロケールを明示して呼ぶ版（グローバル関数）。マクロの中では描画中の context が
# 見えないため、こちらに locale を渡す。


def fmt_date(value: date | datetime | None, locale: LocaleConfig | str | None = None) -> str:
    if value is None:
        return "—"
    d = _as_jst(value).date() if isinstance(value, datetime) else value
    return format_date(d, _code_of(locale))


def fmt_date_short(value: date | datetime | None, locale: LocaleConfig | str | None = None) -> str:
    if value is None:
        return "—"
    d = _as_jst(value).date() if isinstance(value, datetime) else value
    return format_date_short(d, _code_of(locale))


def fmt_datetime(value: datetime | None, locale: LocaleConfig | str | None = None) -> str:
    return "—" if value is None else format_datetime(_as_jst(value), _code_of(locale))


def fmt_time(value: time | datetime | None, locale: LocaleConfig | str | None = None) -> str:
    if value is None:
        return "—"
    t = _as_jst(value).time() if isinstance(value, datetime) else value
    return format_time(t, _code_of(locale))


def fmt_number(value: int | float | None, locale: LocaleConfig | str | None = None) -> str:
    return "—" if value is None else format_number(value, _code_of(locale))


def fmt_money(value: int | None, locale: LocaleConfig | str | None = None) -> str:
    return "—" if value is None else format_money(int(value), _code_of(locale))


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
            # ロケール別（ADR 0016）
            date_l=date_l,
            date_short_l=date_short_l,
            datetime_l=datetime_l,
            time_l=time_l,
            weekday_l=weekday_l,
            number_l=number_l,
            money_l=money_l,
            og_locale=og_locale_for,
        )
        site = ws.site
        self.catalogs: dict[str, Catalog] = load_catalogs(
            ws.i18n_dir,
            [lc.code for lc in site.locale_list],
            default_code=site.default_locale.code,
        )

        def translate_in(locale: LocaleConfig | str | None, key: str, **params: object) -> str:
            """ロケールを明示して文言を引く。マクロの中から使う。"""
            code = _code_of(locale) or site.default_locale.code
            catalog = self.catalogs.get(code) or self.catalogs[site.default_locale.code]
            return catalog.get(key, **params)

        @pass_context
        def translate(ctx: Any, key: str, **params: object) -> str:
            """描画中のページのロケールで文言を引く。無ければ既定ロケールに落ちる。"""
            return translate_in(_ctx_code(ctx), key, **params)

        analytics = analytics_snippet(ws.site.analytics.provider, ws.secrets.cf_web_analytics_token)
        self.env.globals.update(
            site=ws.site,
            base_url=ws.site.base_url,
            analytics=Markup(analytics),
            site_verification=ws.secrets.google_site_verification or "",
            build_time=self.now,
            url_for=ws.site.url,
            t=translate,
            t_in=translate_in,
            locales=site.locale_list,
            default_locale=site.default_locale,
            fmt_date=fmt_date,
            fmt_date_short=fmt_date_short,
            fmt_datetime=fmt_datetime,
            fmt_time=fmt_time,
            fmt_number=fmt_number,
            fmt_money=fmt_money,
        )
        maker = getattr(service, "pii_policy", None)
        base_policy: PiiPolicy = (maker(ws) if maker else None) or default_jp_gov_policy()
        # 運営者自身の連絡先は第三者の個人情報ではないので許可する。
        contact = (ws.site.operator.contact or "").strip()
        self.pii_policy = allow_also(base_policy, emails=[contact], phones=[contact])

    def hreflang_links(self, meta: PageMeta) -> list[dict[str, str]]:
        """hreflang として出す各言語版の一覧。既定ロケール版を x-default にする（ADR 0016）。"""
        site = self.ws.site
        if not site.multilingual or not meta.alternates:
            return []
        base = site.base_url
        links = [
            {"hreflang": lc.lang, "href": f"{base}{meta.alternates[lc.code]}"}
            for lc in site.locale_list
            if lc.code in meta.alternates
        ]
        default_path = meta.alternates.get(site.default_locale.code)
        if default_path:
            links.append({"hreflang": "x-default", "href": f"{base}{default_path}"})
        return links

    def render_page(self, page: Page) -> str:
        template = self.env.get_template(page.template)
        hreflangs = self.hreflang_links(page.meta)
        context = dict(page.context)
        # エンジンが渡す値はサービスの context より優先する（不変条件を壊させない）
        context.update(
            page=page,
            meta=page.meta,
            trust=page.trust,
            locale=self.ws.site.locale(page.meta.locale),
            hreflangs=hreflangs,
        )
        html = template.render(**context)
        problems = verify_page_html(html)
        problems += preflight.check_page_html(
            html,
            path=page.meta.path,
            noindex=page.meta.noindex,
            expect_hreflang=bool(hreflangs),
        )
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
        problems = preflight.check_pages(
            pages, locales=ws.site.locale_list, default_code=ws.site.default_locale.code
        )
        if problems:
            raise BuildError("ロケールの対応づけに問題がある: " + "; ".join(problems[:5]))
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

        for code, catalog in sorted(self.catalogs.items()):
            if catalog.misses:
                sample = ", ".join(sorted(catalog.misses)[:5])
                result.warnings.append(f"未翻訳の文言 {len(catalog.misses)} 件（{code}）: {sample}")

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
        multilingual = self.ws.site.multilingual
        rows = []
        for p in pages:
            if p.meta.noindex or not p.meta.path.endswith(".html"):
                continue
            lastmod = p.trust.updated_at.astimezone(JST).date().isoformat()
            # 多言語のときは各言語版を url の中で示す（検索エンジンの推奨する書き方）
            alts = "".join(
                f'<xhtml:link rel="alternate" hreflang="{link["hreflang"]}" href="{link["href"]}"/>'
                for link in self.hreflang_links(p.meta)
            )
            rows.append(
                f"  <url><loc>{base}{p.meta.url_path}</loc><lastmod>{lastmod}</lastmod>"
                f"<changefreq>{p.meta.changefreq}</changefreq>"
                f"<priority>{p.meta.priority:.1f}</priority>{alts}</url>"
            )
        xhtml = ' xmlns:xhtml="http://www.w3.org/1999/xhtml"' if multilingual else ""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"{xhtml}>\n'
            + "\n".join(rows)
            + "\n</urlset>\n"
        )
