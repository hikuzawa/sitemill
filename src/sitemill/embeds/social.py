"""公式 SNS の埋め込み。埋め込み機能経由のみで転載はしない。今フェーズは YouTube 以外は設計のみ。"""

from __future__ import annotations

import re
from html import escape

from sitemill.models import Embed, EmbedKind

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def youtube_embed(video_id: str, *, title: str, channel: str) -> Embed:
    """YouTube の公式埋め込み（プライバシー強化モード）。channel は公式アカウント名。"""
    if not _YT_ID.match(video_id):
        raise ValueError(f"YouTube の動画 ID として不正: {video_id}")
    return Embed(
        kind=EmbedKind.youtube,
        provider="YouTube",
        src_url=f"https://www.youtube-nocookie.com/embed/{video_id}",
        link_url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        attribution=f"動画: {channel}（YouTube）",
        license_note="YouTube の埋め込み機能を利用。動画の転載ではない",
        width=560,
        height=315,
    )


def instagram_embed(post_url: str, *, title: str, account: str) -> Embed:
    """Instagram の公式埋め込み。oEmbed の登録が要るため次フェーズで実装。"""
    raise NotImplementedError("Instagram の埋め込みは次フェーズ（oEmbed の利用登録が必要）")


def x_embed(post_url: str, *, title: str, account: str) -> Embed:
    """X（旧 Twitter）の公式埋め込み。oEmbed を使う予定で次フェーズに実装。"""
    raise NotImplementedError("X の埋め込みは次フェーズ")


def render_social(embed: Embed) -> str:
    if embed.kind is not EmbedKind.youtube or not embed.src_url:
        raise ValueError("render_social は YouTube の埋め込みだけを描画する")
    return (
        '<figure class="sm-embed sm-embed-video" data-embed="youtube">'
        f'<iframe src="{escape(embed.src_url)}" title="{escape(embed.title)}" '
        f'width="{embed.width}" '
        f'height="{embed.height}" style="border:0;max-width:100%" loading="lazy" '
        'allow="accelerometer; encrypted-media; picture-in-picture" allowfullscreen></iframe>'
        f"<figcaption>{escape(embed.attribution)}</figcaption></figure>"
    )
