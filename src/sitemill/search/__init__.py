"""Search Console の取り込みと集計（ADR 0023）。

`client` が API、`store` が置き場所、`collect` が段取り、`report` が集計と文面。
第 2 段階（LLM による改修の提案・適用）はここには無い（ADR 0014 §6 の着手条件待ち）。
"""

from sitemill.search.client import SearchConsole, SearchConsoleError, load_service_account
from sitemill.search.collect import (
    fetch_performance,
    fetch_sitemaps,
    host_variants,
    inspect_urls,
    inspect_variants,
    pick_urls,
    sitemap_urls,
)
from sitemill.search.report import Summary, markdown, summarise
from sitemill.search.store import Fact, SearchStore

__all__ = [
    "Fact",
    "SearchConsole",
    "SearchConsoleError",
    "SearchStore",
    "Summary",
    "fetch_performance",
    "fetch_sitemaps",
    "host_variants",
    "inspect_urls",
    "inspect_variants",
    "load_service_account",
    "markdown",
    "pick_urls",
    "sitemap_urls",
    "summarise",
]
