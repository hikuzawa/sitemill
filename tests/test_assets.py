"""ライセンス通過画像のパイプライン（ADR 0020）。外部アクセスはせず respx でモックする。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from sitemill.assets import Asset, AssetError, AssetPolicy, AssetStore, fetch_asset
from sitemill.assets.commons import (
    category_files,
    category_name,
    find_category_links,
    read_file_page,
    thumbnail_url,
)
from sitemill.assets.terms import read_terms
from sitemill.build.preflight import check_images
from sitemill.fetch.client import PoliteClient
from sitemill.models import LicenseId, LicenseVerdict

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 2000  # 中身は問わない。大きさだけ通す
IMG_URL = "https://upload.wikimedia.org/wikipedia/commons/a/b/Ritsurin.jpg"


def _granted() -> LicenseVerdict:
    return LicenseVerdict.granted(
        LicenseId.CC_BY_4_0,
        evidence_url="https://commons.wikimedia.org/wiki/File:Ritsurin.jpg",
        evidence_text="Creative Commons Attribution 4.0",
        credit_text="Ritsurin by 撮影者（CC BY 4.0）/ Wikimedia Commons",
    )


def _client() -> PoliteClient:
    return PoliteClient("sitemill/test", jitter=0.0, sleep=lambda _s: None)


@pytest.fixture
def image() -> None:
    respx.get("https://upload.wikimedia.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(IMG_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )


# --- 取得と保存 -------------------------------------------------------------


@respx.mock
def test_licensed_image_is_saved_with_its_provenance(image: None, tmp_path: Path) -> None:
    store = AssetStore(tmp_path, "ritsurin")
    with _client() as client:
        asset = fetch_asset(
            IMG_URL,
            _granted(),
            AssetPolicy(),
            store,
            client=client,
            alt_text="栗林公園の池",
            page_url="https://commons.wikimedia.org/wiki/File:Ritsurin.jpg",
        )
    assert asset.local_path.is_file() and asset.local_path.suffix == ".png"
    assert asset.usable and asset.license is not None and asset.license.allowed

    saved = store.load()
    assert list(saved) == [asset.asset_id]
    kept = saved[asset.asset_id]
    assert kept.source_url == IMG_URL
    assert kept.page_url == "https://commons.wikimedia.org/wiki/File:Ritsurin.jpg"
    assert kept.credit_text.startswith("Ritsurin by")
    assert kept.license is not None and kept.license.license_id is LicenseId.CC_BY_4_0
    assert kept.alt_text == "栗林公園の池"


@respx.mock
def test_denied_license_is_never_fetched(image: None, tmp_path: Path) -> None:
    """既定は不採用。判定を通っていない画像はネットワークに触る前に拒む。"""
    store = AssetStore(tmp_path, "x")
    with _client() as client, pytest.raises(AssetError, match="ライセンス未通過"):
        fetch_asset(
            IMG_URL,
            LicenseVerdict.denied("明示的なライセンス表記が見つからない"),
            AssetPolicy(),
            store,
            client=client,
        )
    assert [c.request.url for c in respx.calls] == []


@respx.mock
def test_policy_rejects_by_type_and_size(tmp_path: Path) -> None:
    respx.get("https://upload.wikimedia.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://upload.wikimedia.org/a.pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        )
    )
    respx.get("https://upload.wikimedia.org/tiny.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )
    store = AssetStore(tmp_path, "x")
    with _client() as client:
        with pytest.raises(AssetError, match="扱わない種類"):
            fetch_asset(
                "https://upload.wikimedia.org/a.pdf",
                _granted(),
                AssetPolicy(),
                store,
                client=client,
            )
        with pytest.raises(AssetError, match="小さすぎる"):
            fetch_asset(
                "https://upload.wikimedia.org/tiny.png",
                _granted(),
                AssetPolicy(),
                store,
                client=client,
            )


@respx.mock
def test_service_exclusion_patterns(image: None, tmp_path: Path) -> None:
    policy = AssetPolicy(exclude_patterns=("/commons/a/",))
    store = AssetStore(tmp_path, "x")
    with _client() as client, pytest.raises(AssetError, match="方針で除外"):
        fetch_asset(IMG_URL, _granted(), policy, store, client=client)


# --- Wikimedia Commons ------------------------------------------------------

CATEGORY_HTML = """<html><body><div id="mw-category-media">
<a href="/wiki/File:Ritsurin_Garden_01.jpg">Ritsurin Garden 01.jpg</a>
<a href="/wiki/File:Ritsurin_Garden_02.jpg?uselang=ja">Ritsurin Garden 02.jpg</a>
<a href="/wiki/Category:Takamatsu">Takamatsu</a>
<a href="/wiki/Special:Search">検索</a>
</div></body></html>"""

FILE_PAGE = """<html><body>
<div id="file"><a href="//upload.wikimedia.org/wikipedia/commons/a/b/Ritsurin.jpg">
<img src="//upload.wikimedia.org/thumb.jpg"></a></div>
<table class="fileinfotpl-type-information">
<tr><td id="fileinfotpl_aut" class="fileinfo-paramfield">Author</td>
<td>Example Photographer</td></tr>
</table>
<table class="layouttemplate licensetpl">
<tr><td>{license}</td></tr>
</table>
<footer><p>Text is available under the
<a href="https://creativecommons.org/licenses/by-sa/4.0/">Creative Commons
Attribution-ShareAlike License</a>.</p></footer>
</body></html>"""

CC_BY = '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
CC_BY_SA = '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>'
CC0 = '<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0 1.0</a>'
PD_OLD = "<p>This work is in the public domain in Japan (PD-Japan).</p>"


def test_category_page_yields_file_pages() -> None:
    urls = category_files(CATEGORY_HTML)
    assert urls == [
        "https://commons.wikimedia.org/wiki/File:Ritsurin_Garden_01.jpg",
        "https://commons.wikimedia.org/wiki/File:Ritsurin_Garden_02.jpg",
    ]


def test_cc_by_file_is_adopted_with_author_and_image_url() -> None:
    page = read_file_page(
        FILE_PAGE.format(license=CC_BY), "https://commons.wikimedia.org/wiki/File:Ritsurin.jpg"
    )
    assert page.verdict.allowed
    assert page.verdict.license_id is LicenseId.CC_BY_4_0
    assert page.image_url == "https://upload.wikimedia.org/wikipedia/commons/a/b/Ritsurin.jpg"
    assert page.author == "Example Photographer"  # ラベル（Author）ではなく値のセル
    assert "Example Photographer" in page.credit_text and "CC BY 4.0" in page.credit_text


def test_site_footer_does_not_poison_the_license_reading() -> None:
    """Commons の全ページ下部に CC BY-SA へのリンクがある。全体にかけると必ず不採用になる。"""
    page = read_file_page(
        FILE_PAGE.format(license=CC_BY), "https://commons.wikimedia.org/wiki/File:Ritsurin.jpg"
    )
    assert page.verdict.allowed  # ライセンス欄だけを見ているので footer に引っ張られない


def test_share_alike_file_is_rejected() -> None:
    page = read_file_page(
        FILE_PAGE.format(license=CC_BY_SA), "https://commons.wikimedia.org/wiki/File:X.jpg"
    )
    assert not page.verdict.allowed
    assert page.licenses_found == ("CC BY-SA（継承）",)
    assert "ホワイトリストに無い" in page.verdict.reason


def test_cc0_file_is_adopted() -> None:
    page = read_file_page(
        FILE_PAGE.format(license=CC0), "https://commons.wikimedia.org/wiki/File:X.jpg"
    )
    assert page.verdict.allowed and page.verdict.license_id is LicenseId.CC0_1_0


def test_public_domain_tag_is_adopted() -> None:
    """継承義務が無いので受け入れる（ADR 0005 追記、2026-09-12 の事業側の判断）。"""
    page = read_file_page(
        FILE_PAGE.format(license=PD_OLD), "https://commons.wikimedia.org/wiki/File:X.jpg"
    )
    assert page.verdict.allowed and page.verdict.license_id is LicenseId.PUBLIC_DOMAIN


def test_public_domain_only_in_one_country_is_rejected() -> None:
    """「米国ではパブリックドメインだが日本では保護期間内」を緩い一致で拾わない。"""
    box = (
        "<p>This work is in the public domain in the United States, but it may "
        "not be in the public domain in other jurisdictions.</p>"
    )
    page = read_file_page(
        FILE_PAGE.format(license=box), "https://commons.wikimedia.org/wiki/File:X.jpg"
    )
    assert not page.verdict.allowed


def test_older_cc_by_versions_are_adopted() -> None:
    for box, expected in (
        (
            '<a href="https://creativecommons.org/licenses/by/2.5/">CC BY 2.5</a>',
            LicenseId.CC_BY_2_5,
        ),
        (
            '<a href="https://creativecommons.org/licenses/by/2.0/">CC BY 2.0</a>',
            LicenseId.CC_BY_2_0,
        ),
        (
            '<a href="https://creativecommons.org/licenses/by/3.0/">CC BY 3.0</a>',
            LicenseId.CC_BY_3_0,
        ),
    ):
        page = read_file_page(
            FILE_PAGE.format(license=box), "https://commons.wikimedia.org/wiki/File:X.jpg"
        )
        assert page.verdict.allowed and page.verdict.license_id is expected, box


def test_public_domain_mark_is_adopted() -> None:
    box = '<a href="https://creativecommons.org/publicdomain/mark/1.0/">PD Mark</a>'
    page = read_file_page(
        FILE_PAGE.format(license=box), "https://commons.wikimedia.org/wiki/File:X.jpg"
    )
    assert page.verdict.allowed and page.verdict.license_id is LicenseId.PD_MARK_1_0


def test_every_whitelisted_license_is_free_of_share_alike() -> None:
    """継承つきを足すとサイトに取り消せない義務が生じる。ホワイトリストの不変条件として固定する。"""
    from sitemill.models.license import NO_SHARE_ALIKE, WHITELIST

    assert WHITELIST == NO_SHARE_ALIKE
    assert all("SA" not in lic.value.upper().replace("-", "") for lic in WHITELIST)


MULTI_LICENSE = """<html><body>
<div id="file"><a href="//upload.wikimedia.org/wikipedia/commons/6/6b/Pano.jpg?utm_source=x">
<img src="//upload.wikimedia.org/thumb.jpg"></a></div>
<table class="layouttemplate licensetpl"><tr><td>
<a href="https://www.gnu.org/licenses/fdl-1.3.html">GNU Free Documentation License</a>
</td></tr></table>
<table class="layouttemplate licensetpl"><tr><td>{first}</td></tr></table>
<table class="layouttemplate licensetpl"><tr><td>{second}</td></tr></table>
</body></html>"""


def test_multi_licensed_file_is_adopted_on_the_permissive_box() -> None:
    """Commons の多重ライセンスは普通。箱をまとめると制限側が許可側を潰すので箱ごとに判定する。"""
    page = read_file_page(
        MULTI_LICENSE.format(first=CC_BY_SA, second=CC_BY),
        "https://commons.wikimedia.org/wiki/File:Pano.jpg",
    )
    assert page.verdict.allowed and page.verdict.license_id is LicenseId.CC_BY_4_0
    assert page.licenses_found == ("GFDL", "CC BY-SA（継承）", "CC-BY-4.0")


def test_multi_licensed_file_with_no_permissive_box_is_rejected_with_a_readable_reason() -> None:
    page = read_file_page(
        MULTI_LICENSE.format(first=CC_BY_SA, second=CC_BY_SA),
        "https://commons.wikimedia.org/wiki/File:Pano.jpg",
    )
    assert not page.verdict.allowed
    assert page.verdict.reason == "ホワイトリストに無いライセンスのみ（GFDL、CC BY-SA（継承））"


def test_tracking_query_is_stripped_and_a_thumbnail_is_used() -> None:
    """原本は 40MB を超えるパノラマもある。サイトに載せるのは縮小画像で足りる。"""
    page = read_file_page(
        MULTI_LICENSE.format(first=CC_BY, second=CC_BY),
        "https://commons.wikimedia.org/wiki/File:Pano.jpg",
    )
    assert page.image_url == "https://upload.wikimedia.org/wikipedia/commons/6/6b/Pano.jpg"
    assert page.usable_url(800) == (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/Pano.jpg/800px-Pano.jpg"
    )


def test_svg_thumbnails_become_png() -> None:
    assert (
        thumbnail_url("https://upload.wikimedia.org/wikipedia/commons/2/22/Logo.svg", 600)
        == "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Logo.svg/600px-Logo.svg.png"
    )


def test_thumbnail_url_returns_none_for_an_unexpected_layout() -> None:
    assert thumbnail_url("https://example.com/photo.jpg") is None


def test_file_without_a_license_box_is_rejected() -> None:
    page = read_file_page("<html><body><p>説明のみ</p></body></html>", "https://c/wiki/File:X.jpg")
    assert not page.verdict.allowed and "ライセンス欄が見つからない" in page.verdict.reason


# --- 自治体・観光協会の素材ページ -------------------------------------------


def test_terms_page_with_cc_by_is_adopted() -> None:
    html = """<html><body><h1>画像の利用について</h1>
    <p>本ページの写真は<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>
    で提供します。出典を明記してご利用ください。</p></body></html>"""
    verdict = read_terms(html, "https://kagawa.example/photo/", credit_name="香川県観光協会")
    assert verdict.allowed
    assert verdict.verdict.credit_text is not None


def test_terms_page_requiring_permission_is_rejected_even_with_cc_by() -> None:
    """条件に人の判断が要るものは、自動で回す仕組みに載せられない。"""
    html = """<html><body><p>写真は<a href="https://creativecommons.org/licenses/by/4.0/">
    CC BY 4.0</a>です。ただし商用利用は事前に申請が必要です。</p></body></html>"""
    verdict = read_terms(html, "https://kagawa.example/photo/", credit_name="香川県")
    assert not verdict.allowed
    assert "人の判断" in verdict.reason and "申請" in verdict.reason


def test_terms_page_without_a_license_is_rejected() -> None:
    html = "<html><body><p>写真の無断転載を禁じます。</p></body></html>"
    verdict = read_terms(html, "https://kagawa.example/", credit_name="香川県")
    assert not verdict.allowed


# --- ビルド時の検査 ---------------------------------------------------------


def _asset(asset_id: str, *, own_work: bool = False) -> Asset:
    return Asset(
        asset_id=asset_id,
        source_url="https://example.com/x.png",
        local_path=Path("x.png"),
        content_type="image/png",
        byte_size=2000,
        license=None if own_work else _granted(),
        fetched_at=datetime(2026, 9, 12, 6, 10, tzinfo=UTC),
        credit_text="",
        own_work=own_work,
    )


def test_unregistered_images_stop_the_build() -> None:
    html = '<html><body><img src="/static/photo.jpg" alt="写真"></body></html>'
    problems = check_images(html, path="spots/x/index.html", registered=set())
    assert problems and "出典の分からない画像" in problems[0]


def test_registered_images_pass() -> None:
    html = '<img src="/assets/abc.jpg" alt="a" data-sitemill-asset="abc123">'
    assert check_images(html, path="p", registered={"abc123"}) == []


def test_image_with_an_unknown_asset_id_stops_the_build() -> None:
    html = '<img src="/assets/abc.jpg" data-sitemill-asset="deadbeef">'
    problems = check_images(html, path="p", registered={"abc123"})
    assert problems and "登録されていない資産 id" in problems[0]


def test_pages_without_images_pass() -> None:
    assert (
        check_images("<html><body><p>写真なし</p></body></html>", path="p", registered=set()) == []
    )


def test_own_work_is_usable_without_a_license_verdict() -> None:
    """自作の図版は第三者の著作物ではないので、ライセンス判定を通さなくてよい。"""
    assert _asset("own1", own_work=True).usable
    assert _asset("third1").usable


def test_asset_without_a_license_and_not_own_work_is_not_usable() -> None:
    asset = _asset("x")
    asset.license = LicenseVerdict.denied("表記なし")
    assert not asset.usable


# --- クレジットの描画 -------------------------------------------------------


def test_photo_figure_renders_the_credit_and_the_build_marker(tmp_path: Path) -> None:
    """写真を出すときは作者・ライセンス・出どころ・取得日を必ず添える（ADR 0020）。"""
    from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

    from sitemill.build.site import datetime_ja, fmt_date, fmt_datetime, fmt_number, number

    env = Environment(
        loader=PackageLoader("sitemill", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    env.filters.update(datetime_ja=datetime_ja, number=number)
    env.globals.update(
        fmt_datetime=fmt_datetime,
        fmt_number=fmt_number,
        t_has=lambda *_a, **_k: False,
        t_in=lambda _locale, key, **kw: {
            "asset.credit": "{author}（{license}）",
            "asset.credit_source": "出典ページ",
            "asset.fetched": "取得: {when}",
        }[key].format(**kw),
        fmt_date=fmt_date,
    )
    macros = env.get_template("sitemill/macros.html").module
    asset = Asset(
        asset_id="95e759ec15561df0",
        source_url="https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/X.JPG/1280px-X.JPG",
        local_path=tmp_path / "x.jpg",
        content_type="image/jpeg",
        byte_size=419173,
        license=_granted(),
        fetched_at=datetime(2026, 9, 12, 8, 28, tzinfo=UTC),  # JST では 9/12 17:28
        credit_text=(
            "Benesse House Beach 2025.JPG by Fotointheworld（CC BY 4.0）/ Wikimedia Commons"
        ),
        alt_text="ベネッセハウス ミュージアム",
        page_url="https://commons.wikimedia.org/wiki/File:Benesse_House_Beach_2025.JPG",
    )
    html = macros.photo_figure(asset, "ja", "/static/assets/95e759ec15561df0.jpg")
    assert 'data-sitemill-asset="95e759ec15561df0"' in html
    assert "Fotointheworld（CC BY 4.0）" in html  # ライセンス名は言語に依らない短い表記
    assert 'href="https://commons.wikimedia.org/wiki/File:Benesse_House_Beach_2025.JPG"' in html
    assert "取得: 2026年9月12日" in html  # JST に直して出す
    assert 'alt="ベネッセハウス ミュージアム"' in html
    # 描画した HTML はビルド時検査を通る
    assert check_images(html, path="p", registered={"95e759ec15561df0"}) == []


def test_credit_falls_back_to_the_stored_text_when_the_author_is_unknown(tmp_path: Path) -> None:
    """作者が取れない画像もある。そのときは保存してあるクレジット文をそのまま出す。"""
    from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

    from sitemill.build.site import datetime_ja, fmt_date, fmt_datetime, fmt_number, number

    env = Environment(
        loader=PackageLoader("sitemill", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    env.filters.update(datetime_ja=datetime_ja, number=number)
    env.globals.update(
        fmt_date=fmt_date,
        fmt_datetime=fmt_datetime,
        fmt_number=fmt_number,
        t_has=lambda *_a, **_k: False,
        t_in=lambda _locale, key, **kw: {"asset.fetched": "取得: {when}"}[key].format(**kw),
    )
    macros = env.get_template("sitemill/macros.html").module
    asset = Asset(
        asset_id="abc123",
        source_url="https://upload.wikimedia.org/x.jpg",
        local_path=tmp_path / "x.jpg",
        content_type="image/jpeg",
        byte_size=2000,
        license=_granted(),
        fetched_at=datetime(2026, 9, 12, 8, 28, tzinfo=UTC),
        credit_text="出典: 香川県（CC BY 4.0）",
        alt_text="写真",
    )
    html = macros.photo_figure(asset, "ja", "/static/assets/abc123.jpg")
    assert "出典: 香川県（CC BY 4.0）" in html


# --- カテゴリを推測せず辿る -------------------------------------------------

WIKIPEDIA_ARTICLE = """<html><body>
<h1>栗林公園</h1>
<div class="sisterproject">
<a href="https://commons.wikimedia.org/wiki/Category:Ritsurin_Garden">ウィキメディア・コモンズ</a>
</div>
<a href="//commons.wikimedia.org/wiki/Category:Ritsurin_Garden?uselang=ja">同じカテゴリ</a>
<a href="https://commons.wikimedia.org/wiki/File:X.jpg">ファイル</a>
<a href="https://ja.wikipedia.org/wiki/高松市">高松市</a>
</body></html>"""


def test_category_links_are_followed_not_guessed() -> None:
    """カテゴリ名は推測しない。ページから実際に張られているリンクだけを辿る。"""
    links = find_category_links(WIKIPEDIA_ARTICLE)
    assert links == ["https://commons.wikimedia.org/wiki/Category:Ritsurin_Garden"]
    assert category_name(links[0]) == "Ritsurin Garden"


def test_no_category_link_means_no_category() -> None:
    assert find_category_links("<html><body><p>リンクなし</p></body></html>") == []
