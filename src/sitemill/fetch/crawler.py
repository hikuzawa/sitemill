"""Source 単位の巡回。seed → follow 規則で辿り、変化した URL に pending_extract を立てる。"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field

from sitemill.diff.hasher import content_hash
from sitemill.diff.normalize import normalize_for_hash
from sitemill.diff.state import CrawlState
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links, host_allowed, host_of
from sitemill.models import FollowRule, PageKind, Source
from sitemill.store.raw import RawCache

log = logging.getLogger(__name__)


@dataclass
class CrawledPage:
    url: str
    kind: PageKind
    status: int
    changed: bool = False
    not_modified: bool = False
    error: str | None = None


@dataclass
class CrawlSummary:
    source_id: str
    pages: list[CrawledPage] = field(default_factory=list)

    def count(self, key: str) -> int:
        return sum(
            1
            for p in self.pages
            if {
                "fetched": p.status == 200,
                "changed": p.changed,
                "unchanged": p.status in (200, 304) and not p.changed,
                "not_modified": p.not_modified,
                "errors": p.error is not None and not p.not_modified,
            }[key]
        )


def _allowed_hosts(source: Source) -> list[str]:
    hosts = list(source.allow_hosts)
    for page in source.pages:
        h = host_of(page.url)
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def crawl_source(
    source: Source,
    client: PoliteClient,
    state: CrawlState,
    raw: RawCache,
    *,
    max_pages: int | None = None,
    force: bool = False,
) -> CrawlSummary:
    """seed ページと follow 規則に従って巡回し、状態と生 HTML キャッシュを更新する。"""
    summary = CrawlSummary(source_id=source.id)
    if not source.crawlable:
        log.info("%s は policy=%s のため巡回しない", source.id, source.policy)
        return summary

    limit = min(max_pages or source.max_pages, source.max_pages)
    hosts = _allowed_hosts(source)
    queue: deque[tuple[str, PageKind, list[FollowRule]]] = deque(
        (p.url, p.kind, list(p.follow)) for p in source.pages
    )
    seen: set[str] = set()

    while queue and len(summary.pages) < limit:
        url, kind, follows = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        if not host_allowed(url, hosts):
            log.info("許可ホスト外のため取得しない: %s", url)
            continue

        st = state.get_or_create(url, source.id, kind.value)
        cached = raw.load(source.id, url)
        use_conditional = cached is not None and not force
        result = client.get(
            url,
            etag=st.etag if use_conditional else None,
            last_modified=st.last_modified if use_conditional else None,
            delay=source.delay_seconds,
        )
        st.seen_count += 1
        st.status = result.status
        page = CrawledPage(url=url, kind=kind, status=result.status)

        html: str | None = None
        if result.not_modified:
            st.fetched_at = result.fetched_at
            st.error = None
            page.not_modified = True
            html = raw.load_text(source.id, url)
        elif result.ok:
            st.fetched_at = result.fetched_at
            st.etag = result.etag
            st.last_modified = result.last_modified
            st.error = None
            html = result.text
            digest = content_hash(
                normalize_for_hash(html, source.content_selector, source.ignore_patterns)
            )
            if digest != st.content_hash:
                st.content_hash = digest
                st.changed_at = result.fetched_at
                st.pending_extract = True
                page.changed = True
            raw.save(
                source.id,
                url,
                result.content,
                {
                    "fetched_at": result.fetched_at.isoformat(),
                    "encoding": result.encoding,
                    "status": result.status,
                    "final_url": result.final_url,
                    "content_type": result.headers.get("content-type"),
                    "content_hash": digest,
                    "kind": kind.value,
                },
            )
        else:
            st.error = result.error or f"HTTP {result.status}"
            st.error_count += 1
            page.error = st.error
        summary.pages.append(page)

        if html and follows:
            base = result.final_url if result.ok else url
            links = extract_links(html, base)
            for rule in follows:
                pattern = re.compile(rule.pattern)
                matched = [ln.url for ln in links if pattern.search(ln.url)]
                if rule.max_links is not None:
                    matched = matched[: rule.max_links]
                for link in matched:
                    if link not in seen:
                        queue.append((link, rule.kind, []))

    if queue:
        log.info("%s: max_pages=%d に達したため %d 件を今回見送り", source.id, limit, len(queue))
    return summary
