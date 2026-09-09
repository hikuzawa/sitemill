"""HTML からのリンク抽出とホスト制限。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from selectolax.parser import HTMLParser

_SKIP_PREFIX = ("mailto:", "tel:", "javascript:", "#", "data:")


@dataclass(frozen=True)
class Link:
    url: str
    text: str


def extract_links(html: str, base_url: str) -> list[Link]:
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    if base is not None and base.attributes.get("href"):
        base_url = urljoin(base_url, base.attributes["href"])
    out: list[Link] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_PREFIX):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(Link(url=url, text=a.text(separator=" ", strip=True)))
    return out


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def host_allowed(url: str, allow_hosts: list[str]) -> bool:
    host = host_of(url)
    if not host:
        return False
    for allowed in allow_hosts:
        a = allowed.lower().lstrip(".")
        if host == a or host.endswith("." + a):
            return True
    return False
