"""Wikimedia Commons の画像のライセンス判定（ADR 0020）。

**robots.txt の制約**（実測、2026-09-12）: `commons.wikimedia.org` は
  Disallow: /w/        → `/w/api.php` は使えない（API 検索も画像情報 API も不可）
  Disallow: /wiki/Special:  → `Special:FilePath` も使えない
  許可: `/wiki/File:...`、`/wiki/Category:...`、`upload.wikimedia.org`（全許可）
つまり「API で探す」ことはできない。カテゴリページからファイルページを辿り、ファイルページの
HTML からライセンスを読み、画像の実体は upload.wikimedia.org から取る。robots は破らない。

ライセンスの判定は既存の `license.detector.detect_license` を使い回すが、**ファイルページ全体には
かけない**。Commons のページ下部には サイト自体の利用規約（CC BY-SA）へのリンクが必ずあり、
全体にかけると「許可と制限の表記が混在」で必ず不採用になる。ライセンス欄の断片だけを渡す。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urljoin

from selectolax.parser import HTMLParser

from sitemill.license.detector import detect_license
from sitemill.models import LicenseVerdict

log = logging.getLogger(__name__)

COMMONS = "https://commons.wikimedia.org"
_FILE_LINK = re.compile(r"^/wiki/(File|%E3%83%95%E3%82%A1%E3%82%A4%E3%83%AB):", re.I)
# ライセンス欄。Commons のライセンステンプレートが使う入れ物
_LICENSE_SELECTORS = (
    "#mw-imagepage-content-license",
    ".licensetpl",
    "table.licensetpl",
    ".layouttemplate.licensetpl",
    "#wpl-content-license",
    ".fileinfotpl-license",
)
# 作者・出典の欄
_AUTHOR_SELECTORS = ("#fileinfotpl_aut", ".fileinfotpl_aut", "#fileinfotpl_src")
_IMAGE_SELECTORS = ("#file > a", ".fullImageLink > a", "#file a.internal")
UPLOAD_HOST = "https://upload.wikimedia.org/"
# MediaWiki の縮小画像の置き場。`.../commons/a/ab/Name.jpg` →
# `.../commons/thumb/a/ab/Name.jpg/<幅>px-Name.jpg`
_FILE_PATH = re.compile(
    r"^(?P<base>https://upload\.wikimedia\.org/(?P<project>[^/]+)/[^/]+)/"
    r"(?P<a>[0-9a-f])/(?P<ab>[0-9a-f]{2})/(?P<name>[^/?#]+)$"
)
DEFAULT_THUMB_WIDTH = 1280


# 不採用になった箱を人が読める名前にする（報告と /data ページの説明用）。
# 判定そのものは license.detector が行い、ここは表示のためだけに使う。
_BOX_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"licenses/by-sa|BY[\s-]?SA|ShareAlike|継承", re.I), "CC BY-SA（継承）"),
    (re.compile(r"licenses/by-nc|BY[\s-]?NC|NonCommercial|非営利", re.I), "CC BY-NC（非営利）"),
    (re.compile(r"licenses/by-nd|BY[\s-]?ND|NoDerivatives|改変禁止", re.I), "CC BY-ND（改変禁止）"),
    (re.compile(r"GNU Free Documentation|GFDL", re.I), "GFDL"),
    (re.compile(r"public domain|パブリックドメイン|PD-", re.I), "パブリックドメイン表示"),
    (re.compile(r"fair use|フェアユース", re.I), "フェアユース主張"),
)


def box_label(html: str, verdict: LicenseVerdict) -> str:
    """ライセンスの箱 1 つを短い名前で表す。採用できたものはライセンス識別子。"""
    if verdict.license_id is not None:
        return verdict.license_id.value
    for pattern, label in _BOX_LABELS:
        if pattern.search(html):
            return label
    return "判定できない表記"


def strip_query(url: str) -> str:
    """追跡用の query を落とす。Commons は媒体の URL に utm_* を付けて返す。"""
    return url.split("?", 1)[0].split("#", 1)[0]


def thumbnail_url(image_url: str, width: int = DEFAULT_THUMB_WIDTH) -> str | None:
    """原本の URL から縮小画像の URL を作る。作れなければ None。

    原本は 40MB を超えるパノラマもある。サイトに載せるのは縮小画像で足りるし、
    相手サーバにも優しい。URL の組み立ては MediaWiki の決まった配置に従う。
    """
    m = _FILE_PATH.match(strip_query(image_url))
    if m is None:
        return None
    name = m.group("name")
    thumb_name = f"{width}px-{name}"
    if name.lower().endswith(".svg"):
        thumb_name += ".png"  # SVG の縮小は PNG になる
    return f"{m.group('base')}/thumb/{m.group('a')}/{m.group('ab')}/{name}/{thumb_name}"


@dataclass
class CommonsFile:
    """Commons のファイル 1 件。採用可否は `verdict.allowed` で決まる。"""

    page_url: str
    title: str
    image_url: str | None
    author: str | None
    verdict: LicenseVerdict
    licenses_found: tuple[str, ...] = ()  # 箱ごとの判定結果（不採用の理由を説明するため）

    @property
    def credit_text(self) -> str:
        """ページに出すクレジット文。作者名とライセンス名と出どころを必ず含める。"""
        who = self.author or "作者表示なし"
        return f"{self.title} by {who}（{self.verdict.label}）/ Wikimedia Commons"

    def usable_url(self, width: int = DEFAULT_THUMB_WIDTH) -> str | None:
        """取得する URL。縮小画像を作れればそちら、無理なら原本。"""
        if self.image_url is None:
            return None
        return thumbnail_url(self.image_url, width) or strip_query(self.image_url)


def _all_of(tree: HTMLParser, selectors: tuple[str, ...]) -> list[str]:
    """当てはまる要素すべての HTML を返す（重複は除く）。

    Commons のファイルは**多重ライセンス**が普通で、GFDL と CC BY-SA と CC BY が別々の箱で
    並ぶ。箱をまとめて 1 つの文字列にすると、片方の制限表記がもう片方の許可を潰してしまうので、
    箱ごとに判定する。
    """
    out: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        for node in tree.css(selector):
            html = (node.html or "").strip()
            if html and html not in seen:
                seen.add(html)
                out.append(html)
    return out


def _value_beside(tree: HTMLParser, selectors: tuple[str, ...]) -> str:
    """情報テーブルの「値」側のセルを返す。

    Commons の情報テンプレートは `id="fileinfotpl_aut"` を**ラベルのセル**に付ける
    （`<td id="fileinfotpl_aut">Author</td><td>撮影者</td>`）。id の要素をそのまま読むと
    「Author」というラベルを作者名として持ち出してしまう。隣のセルを見る。
    行（`<tr>`）に id が付く書き方もあるので、その場合は最後のセルを値とみなす。
    """
    for selector in selectors:
        for node in tree.css(selector):
            if node.tag == "tr":
                cells = node.css("td")
                if len(cells) >= 2:
                    return cells[-1].html or ""
            sibling = node.next
            while sibling is not None and sibling.tag == "-text":
                sibling = sibling.next
            if sibling is not None and (sibling.html or "").strip():
                return sibling.html or ""
            html = node.html or ""
            if html.strip():
                return html
    return ""


def _plain(html: str, limit: int = 160) -> str:
    text = HTMLParser(html).text(separator=" ") if html else ""
    return " ".join(text.split())[:limit]


def category_files(html: str, *, base_url: str = COMMONS, limit: int = 40) -> list[str]:
    """カテゴリページの HTML から、ファイルページの URL を集める。"""
    out: list[str] = []
    seen: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = node.attributes.get("href") or ""
        if not _FILE_LINK.match(href):
            continue
        url = urljoin(base_url, href.split("?")[0])
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def file_title(page_url: str) -> str:
    return unquote(page_url.rsplit("/", 1)[-1]).split(":", 1)[-1].replace("_", " ")


def read_file_page(html: str, page_url: str) -> CommonsFile:
    """ファイルページの HTML から、ライセンス・作者・画像の URL を読む。

    ライセンス欄が見つからなければ不採用。Commons はテンプレートが多様なので、
    「読めなかった」と「制限つきだった」を区別せず、どちらも不採用にする（既定は不採用）。
    """
    tree = HTMLParser(html)
    title = file_title(page_url)
    boxes = _all_of(tree, _LICENSE_SELECTORS)
    author_html = _value_beside(tree, _AUTHOR_SELECTORS)
    author = _plain(author_html) or None

    image_url: str | None = None
    for selector in _IMAGE_SELECTORS:
        for node in tree.css(selector):
            href = node.attributes.get("href") or ""
            if href.startswith("//"):
                href = "https:" + href
            if href.startswith(UPLOAD_HOST):
                image_url = strip_query(href)
                break
        if image_url:
            break

    credit_name = f"{title} / Wikimedia Commons"
    verdicts = [detect_license(box, page_url, credit_name=credit_name) for box in boxes]
    found = tuple(box_label(box, v) for box, v in zip(boxes, verdicts, strict=True))
    allowed = next((v for v in verdicts if v.allowed), None)
    if allowed is not None:
        verdict = allowed
    elif not boxes:
        verdict = LicenseVerdict.denied("ライセンス欄が見つからない", url=page_url)
    else:
        # ライセンスの記載はあるが、どれもホワイトリストに無い（GFDL・BY-SA・PD など）
        verdict = LicenseVerdict.denied(
            f"ホワイトリストに無いライセンスのみ（{'、'.join(dict.fromkeys(found))}）",
            url=page_url,
        )
    return CommonsFile(
        page_url=page_url,
        title=title,
        image_url=image_url,
        author=author,
        verdict=verdict,
        licenses_found=found,
    )


def fetch_category_files(
    client: Any, category_url: str, *, limit: int = 40
) -> tuple[list[str], str | None]:
    """カテゴリページを取得してファイルページの URL を返す。返り値は (URL 一覧, 失敗理由)。"""
    res = client.get(category_url)
    if not res.ok:
        return [], f"カテゴリを取得できない（status={res.status} {res.error or ''}）"
    return category_files(res.text, limit=limit), None


def fetch_file(client: Any, page_url: str) -> tuple[CommonsFile | None, str | None]:
    """ファイルページを取得してライセンスを判定する。返り値は (結果, 失敗理由)。"""
    res = client.get(page_url)
    if not res.ok:
        return None, f"ファイルページを取得できない（status={res.status} {res.error or ''}）"
    return read_file_page(res.text, page_url), None
