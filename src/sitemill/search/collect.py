"""取得の段取り（ADR 0023）。API の呼び方は `client.py`、置き場所は `store.py`。

やること:

1. 検索パフォーマンスを 3 粒度（検索語 / ページ / ページ×検索語）で取り、日付単位で上書きする
2. サイトマップの状態を取る
3. URL 検査を**順に回す**（1 日の件数を決め、未検査の URL → 最後に検査した日が古い順）

Search Console の数字は 2〜3 日遅れて確定する。毎日「直近 5 日」を取り直すので、遅れて確定した
分も自動で埋まる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from sitemill.clock import jst_now
from sitemill.search.client import INSPECT_PER_DAY, SearchConsole
from sitemill.search.store import GRAINS, Fact, SearchStore

# 既定でさかのぼる日数。Search Console の遅れ（2〜3 日）を含めて取り直す
REFRESH_DAYS = 5
# 初回にさかのぼる日数。Search Console が保持しているのは 16 か月
BACKFILL_DAYS = 480
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)


@dataclass
class FetchResult:
    start: date
    end: date
    rows: dict[str, int] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    sitemaps: list[dict[str, Any]] = field(default_factory=list)
    requests: int = 0


def fetch_performance(
    console: SearchConsole,
    store: SearchStore,
    *,
    days: int = REFRESH_DAYS,
    today: date | None = None,
) -> FetchResult:
    """直近 `days` 日を 3 粒度で取り直す。"""
    end = today or jst_now().date()
    start = end - timedelta(days=days - 1)
    result = FetchResult(start=start, end=end)
    replace = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    for grain, dimensions in GRAINS.items():
        rows, truncated = console.performance(start, end, dimensions)
        facts = [
            Fact(
                day=date.fromisoformat(r.keys[0]),
                keys=tuple(r.keys[1:]),
                clicks=r.clicks,
                impressions=r.impressions,
                ctr=r.ctr,
                position=r.position,
            )
            for r in rows
            if r.keys
        ]
        store.write(grain, facts, replace_days=replace)
        result.rows[grain] = len(facts)
        if truncated:
            result.truncated.append(grain)
    result.requests = console.request_count
    return result


def fetch_sitemaps(console: SearchConsole, store: SearchStore) -> list[dict[str, Any]]:
    rows = console.sitemaps()
    store.write_sitemaps(rows, checked_at=jst_now())
    return rows


def sitemap_urls(dist: Path, base_url: str, *, client: httpx.Client | None = None) -> list[str]:
    """サイトに載っている URL。手元の `dist/sitemap.xml` を先に見て、無ければ公開中のものを引く。"""
    local = dist / "sitemap.xml"
    if local.is_file():
        return _LOC.findall(local.read_text(encoding="utf-8"))
    owned = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        res = client.get(f"{base_url.rstrip('/')}/sitemap.xml")
        return _LOC.findall(res.text) if res.status_code == 200 else []
    finally:
        if owned:
            client.close()


@dataclass
class InspectResult:
    checked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    states: dict[str, int] = field(default_factory=dict)


def pick_urls(urls: list[str], known: dict[str, dict[str, Any]], limit: int) -> list[str]:
    """次に検査する URL。**未検査を先に**、次に検査が古い順。

    1 日の件数を絞って順に回すので、全ページを一巡するのに `全 URL ÷ limit` 日かかる。
    どれだけ回ったかは報告に出す（数えていない分を「インデックスされていない」と読ませない）。
    """
    never = [u for u in urls if u not in known]
    seen = [u for u in urls if u in known]
    seen.sort(key=lambda u: str(known[u].get("checked_on", "")))
    return (never + seen)[:limit]


def inspect_urls(
    console: SearchConsole,
    store: SearchStore,
    urls: list[str],
    *,
    limit: int = INSPECT_PER_DAY,
    now: datetime | None = None,
) -> InspectResult:
    """URL 検査を回して、結果を URL ごとに 1 件ずつ残す。"""
    stamp = now or jst_now()
    known = store.read_urls()
    result = InspectResult()
    for url in pick_urls(urls, known, limit):
        try:
            inspection = console.inspect(url)
        except Exception as e:  # noqa: BLE001 - 1 件の失敗で全体を止めない
            result.failed.append(f"{url}: {e}")
            continue
        status = inspection.get("indexStatusResult", {})
        known[url] = {
            "verdict": status.get("verdict", ""),
            "coverage_state": status.get("coverageState", ""),
            "last_crawl": status.get("lastCrawlTime", ""),
            "google_canonical": status.get("googleCanonical", ""),
            "robots": status.get("robotsTxtState", ""),
            "checked_on": stamp.date().isoformat(),
        }
        result.checked.append(url)
        state = known[url]["coverage_state"] or "（状態なし）"
        result.states[state] = result.states.get(state, 0) + 1
    # サイトから消えた URL は残さない（古い状態が数に混ざる）
    alive = set(urls)
    known = {u: v for u, v in known.items() if u in alive}
    store.write_urls(known, checked_at=stamp)
    return result
