"""計測タグ。Cloudflare Web Analytics のビーコンをトークンがある場合だけ出す（ADR 0007）。"""

from __future__ import annotations

from html import escape


def analytics_snippet(provider: str, token: str | None) -> str:
    if not token:
        return ""
    if provider == "cloudflare":
        return (
            '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
            f'data-cf-beacon=\'{{"token": "{escape(token)}"}}\'></script>'
        )
    return ""
