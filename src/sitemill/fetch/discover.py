"""目的ページの発見。公式トップと sitemap からリンク文字と URL で候補を集める。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.models import Source

log = logging.getLogger(__name__)

DEFAULT_KEYWORDS = r"空き家|空家|空き地|あきや|akiya|空きや"
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


@dataclass
class Candidate:
    url: str
    text: str
    found_on: str
    score: int


def _scan_sitemap(
    client: PoliteClient, url: str, keywords: re.Pattern[str], depth: int = 0
) -> list[str]:
    if depth > 1:
        return []
    res = client.get(url)
    if not res.ok:
        return []
    locs = _LOC.findall(res.text)
    hits: list[str] = []
    for loc in locs:
        if loc.endswith(".xml") and "sitemap" in loc.lower():
            hits.extend(_scan_sitemap(client, loc, keywords, depth + 1))
        elif keywords.search(loc):
            hits.append(loc)
        if len(hits) >= 50:
            break
    return hits


def discover_source(
    source: Source,
    client: PoliteClient,
    *,
    keywords: str = DEFAULT_KEYWORDS,
    max_pages: int = 6,
    use_sitemap: bool = True,
) -> list[Candidate]:
    """候補 URL を score 順に返す。取得するのは公式トップ・既知の seed・sitemap だけ。"""
    kw = re.compile(keywords, re.I)
    found: dict[str, Candidate] = {}
    official_host = host_of(source.official_url)

    def add(url: str, text: str, found_on: str, base: int) -> None:
        text_hit, url_hit = bool(kw.search(text)), bool(kw.search(url))
        if not (text_hit or url_hit):
            return
        score = base + (2 if text_hit else 0) + (1 if url_hit else 0)
        if host_of(url) == official_host:
            score += 1
        cur = found.get(url)
        if cur is None or cur.score < score:
            found[url] = Candidate(url=url, text=text[:80], found_on=found_on, score=score)

    scan = [source.official_url] + [p.url for p in source.pages if p.url != source.official_url]
    for url in scan[:max_pages]:
        res = client.get(url)
        if not res.ok:
            log.info("発見スキャン失敗 %s: %s", url, res.error)
            continue
        for link in extract_links(res.text, res.final_url):
            add(link.url, link.text, url, 0)

    if use_sitemap:
        for sm in client.robots.sitemaps(source.official_url)[:3]:
            for loc in _scan_sitemap(client, sm, kw):
                add(loc, "", sm, 0)

    return sorted(found.values(), key=lambda c: (-c.score, c.url))
