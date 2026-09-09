"""Google Maps Embed API の埋め込み。キーが無ければ外部リンクにフォールバックする（ADR 0008）。"""

from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

from sitemill.models import Embed, EmbedKind

_EMBED_BASE = "https://www.google.com/maps/embed/v1"
_ATTRIBUTION = "地図データ ©Google"
_LICENSE_NOTE = "Google Maps Platform の埋め込み機能を利用。地図画像の転載ではない"


def maps_place_embed(
    query: str,
    *,
    api_key: str | None,
    title: str,
    zoom: int | None = None,
    language: str = "ja",
    region: str = "JP",
) -> Embed:
    """住所や地名の検索結果を地図として埋め込む。"""
    link_url = "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": query})
    src_url: str | None = None
    if api_key:
        params: dict[str, str | int] = {
            "key": api_key,
            "q": query,
            "language": language,
            "region": region,
        }
        if zoom is not None:
            params["zoom"] = zoom
        src_url = f"{_EMBED_BASE}/place?{urlencode(params, quote_via=quote)}"
    return Embed(
        kind=EmbedKind.map,
        provider="Google Maps Embed API",
        src_url=src_url,
        link_url=link_url,
        title=title,
        attribution=_ATTRIBUTION,
        license_note=_LICENSE_NOTE,
    )


def streetview_embed(
    lat: float, lng: float, *, api_key: str | None, title: str, heading: int | None = None
) -> Embed:
    """Street View の埋め込み。緯度経度が要る（ジオコーディング導入までは未使用）。"""
    link_url = "https://www.google.com/maps/@?" + urlencode(
        {"api": 1, "map_action": "pano", "viewpoint": f"{lat},{lng}"}
    )
    src_url: str | None = None
    if api_key:
        params: dict[str, str | int] = {"key": api_key, "location": f"{lat},{lng}"}
        if heading is not None:
            params["heading"] = heading
        src_url = f"{_EMBED_BASE}/streetview?{urlencode(params)}"
    return Embed(
        kind=EmbedKind.streetview,
        provider="Google Maps Embed API",
        src_url=src_url,
        link_url=link_url,
        title=title,
        attribution=_ATTRIBUTION,
        license_note=_LICENSE_NOTE,
    )


def render_embed(embed: Embed) -> str:
    """iframe を返す。src が無い（キー未設定など）ときは外部リンクだけを返す。"""
    label = escape(embed.title)
    link = (
        f'<a class="sm-embed-link" href="{escape(embed.link_url)}" target="_blank" '
        f'rel="noopener noreferrer">{label}（{escape(embed.provider)} で開く）</a>'
    )
    if not embed.src_url:
        return (
            f'<div class="sm-embed sm-embed-fallback" data-embed="{embed.kind.value}">{link}</div>'
        )
    return (
        f'<figure class="sm-embed" data-embed="{embed.kind.value}">'
        f'<iframe src="{escape(embed.src_url)}" title="{label}" width="{embed.width}" '
        f'height="{embed.height}" style="border:0;max-width:100%" loading="lazy" '
        'referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe>'
        f"<figcaption>{escape(embed.attribution)}。{link}</figcaption></figure>"
    )
