import pytest

from sitemill.embeds import (
    instagram_embed,
    maps_place_embed,
    render_embed,
    render_social,
    streetview_embed,
    x_embed,
    youtube_embed,
)
from sitemill.models import EmbedKind


def test_maps_place_embed_with_key_builds_iframe() -> None:
    e = maps_place_embed(
        "長野県東御市本海野", api_key="KEY123", title="東御市本海野の地図", zoom=14
    )
    assert e.kind is EmbedKind.map and e.embeddable
    assert e.src_url is not None
    assert e.src_url.startswith("https://www.google.com/maps/embed/v1/place?")
    assert "key=KEY123" in e.src_url and "zoom=14" in e.src_url and "q=%E9%95%B7" in e.src_url
    html = render_embed(e)
    assert "<iframe" in html and 'loading="lazy"' in html and "地図データ ©Google" in html
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=' in html


def test_maps_embed_without_key_falls_back_to_link() -> None:
    e = maps_place_embed("長野県佐久市", api_key=None, title="佐久市の地図")
    assert not e.embeddable
    html = render_embed(e)
    assert "<iframe" not in html and "sm-embed-fallback" in html and "で開く" in html


def test_streetview_embed() -> None:
    e = streetview_embed(36.36, 138.33, api_key="K", title="街並み", heading=90)
    assert e.kind is EmbedKind.streetview and e.src_url is not None
    assert "location=36.36%2C138.33" in e.src_url and "heading=90" in e.src_url
    assert streetview_embed(1, 2, api_key=None, title="x").src_url is None


def test_youtube_embed_and_unimplemented_providers() -> None:
    e = youtube_embed("dQw4w9WgXcQ", title="東御市の紹介", channel="東御市公式")
    assert e.src_url == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    assert "youtube-nocookie" in render_social(e) and "東御市公式" in render_social(e)
    with pytest.raises(ValueError):
        youtube_embed("bad id!", title="t", channel="c")
    with pytest.raises(NotImplementedError):
        instagram_embed("https://www.instagram.com/p/x/", title="t", account="a")
    with pytest.raises(NotImplementedError):
        x_embed("https://x.com/a/status/1", title="t", account="a")
