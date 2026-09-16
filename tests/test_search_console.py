"""Search Console の取り込みと集計（ADR 0023）。外部アクセスはしない。

API の応答は手で書いた偽物で、`SearchConsole` の代わりに同じ形のものを差し込む。
見るのは「上書きの仕方」「集計の意味」「データが 0 のときの読み方」の 3 つ。
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from sitemill.search import (
    SearchStore,
    fetch_performance,
    inspect_urls,
    load_service_account,
    markdown,
    pick_urls,
    sitemap_urls,
    summarise,
)
from sitemill.search.client import Row
from sitemill.search.store import Fact
from sitemill.settings import SecretsError


class FakeConsole:
    """`SearchConsole` と同じ呼び方ができる偽物。"""

    def __init__(self, rows: dict[tuple[str, ...], list[Row]], inspections: dict[str, str]) -> None:
        self.rows = rows
        self.inspections = inspections
        self.request_count = 0
        self.asked: list[str] = []

    def performance(self, start, end, dimensions, *, row_limit=5000):  # noqa: ANN001, ARG002
        self.request_count += 1
        return self.rows.get(tuple(dimensions), []), False

    def inspect(self, url: str) -> dict:
        self.request_count += 1
        self.asked.append(url)
        state = self.inspections.get(url, "URL is unknown to Google")
        return {
            "indexStatusResult": {
                "verdict": "PASS" if state == "Submitted and indexed" else "NEUTRAL",
                "coverageState": state,
                "lastCrawlTime": "2026-09-13T09:18:51Z",
            }
        }


def _row(day: str, key: str, clicks: int, impressions: int, position: float) -> Row:
    return Row(
        keys=(day, key),
        clicks=clicks,
        impressions=impressions,
        ctr=clicks / impressions if impressions else 0.0,
        position=position,
    )


# --- 鍵の読み込み -----------------------------------------------------------


def test_the_key_can_be_base64_or_json() -> None:
    info = {"client_email": "a@b.iam.gserviceaccount.com", "private_key": "x", "token_uri": "t"}
    raw = json.dumps(info)
    assert load_service_account(raw)["client_email"] == info["client_email"]
    encoded = base64.b64encode(raw.encode()).decode()
    assert load_service_account(encoded)["client_email"] == info["client_email"]


def test_a_missing_key_says_what_to_write() -> None:
    with pytest.raises(SecretsError, match="GOOGLE_SEARCH_CONSOLE_KEY"):
        load_service_account(None)
    with pytest.raises(SecretsError, match="項目がありません"):
        load_service_account(json.dumps({"client_email": "a@b"}))


# --- 取り込み ---------------------------------------------------------------


def test_refetching_a_day_replaces_it_instead_of_adding(tmp_path: Path) -> None:
    """Search Console の数字は 2〜3 日遅れて確定する。取り直しは置き換えでなければならない。"""
    store = SearchStore(tmp_path)
    console = FakeConsole(
        {
            ("date", "query"): [_row("2026-09-10", "香川 開いてる", 0, 5, 40.0)],
            ("date", "page"): [_row("2026-09-10", "https://x/a", 0, 5, 40.0)],
            ("date", "page", "query"): [],
        },
        {},
    )
    fetch_performance(console, store, days=2, today=date(2026, 9, 11))
    console.rows[("date", "query")] = [_row("2026-09-10", "香川 開いてる", 1, 9, 30.0)]
    fetch_performance(console, store, days=2, today=date(2026, 9, 11))

    facts = store.read("query")
    assert len(facts) == 1  # 2 行にならない
    assert (facts[0].impressions, facts[0].clicks) == (9, 1)  # 確定した方が残る


def test_days_outside_the_window_are_kept(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    store.write(
        "page",
        [Fact(date(2026, 8, 1), ("https://x/old",), 1, 10, 0.1, 5.0)],
        replace_days={date(2026, 8, 1)},
    )
    console = FakeConsole({("date", "page"): [_row("2026-09-10", "https://x/a", 0, 5, 40.0)]}, {})
    fetch_performance(console, store, days=1, today=date(2026, 9, 10))
    days = {f.day for f in store.read("page")}
    assert days == {date(2026, 8, 1), date(2026, 9, 10)}


def test_inspection_goes_round_the_site_in_order(tmp_path: Path) -> None:
    """1 日の件数を絞って回す。未検査を先に、次に検査が古い順。"""
    known = {
        "https://x/a": {"checked_on": "2026-09-01"},
        "https://x/b": {"checked_on": "2026-09-05"},
    }
    urls = ["https://x/a", "https://x/b", "https://x/c"]
    assert pick_urls(urls, known, 2) == ["https://x/c", "https://x/a"]


def test_inspection_records_each_url_once(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    console = FakeConsole({}, {"https://x/a": "Submitted and indexed"})
    urls = ["https://x/a", "https://x/b"]
    done = inspect_urls(console, store, urls, limit=2, now=datetime(2026, 9, 14, 9, 0))
    assert done.states == {"Submitted and indexed": 1, "URL is unknown to Google": 1}
    stored = store.read_urls()
    assert stored["https://x/a"]["verdict"] == "PASS"
    assert stored["https://x/b"]["checked_on"] == "2026-09-14"

    # サイトから消えた URL の状態は残さない（古い数が混ざる）
    inspect_urls(console, store, ["https://x/a"], limit=1, now=datetime(2026, 9, 15, 9, 0))
    assert set(store.read_urls()) == {"https://x/a"}


def test_sitemap_urls_come_from_the_built_site(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "sitemap.xml").write_text(
        "<urlset><url><loc>https://x/a</loc></url><url><loc>https://x/b</loc></url></urlset>",
        encoding="utf-8",
    )
    assert sitemap_urls(dist, "https://x") == ["https://x/a", "https://x/b"]


# --- 集計 -------------------------------------------------------------------


def _seed(store: SearchStore) -> None:
    """前の週と今週。ページ a は CTR が低く、b は順位が落ち、c は今週から出た。"""
    prev = [
        Fact(date(2026, 9, 1), ("https://x/a",), 1, 100, 0.01, 8.0),
        Fact(date(2026, 9, 1), ("https://x/b",), 5, 50, 0.10, 4.0),
    ]
    now = [
        Fact(date(2026, 9, 10), ("https://x/a",), 1, 200, 0.005, 8.0),
        Fact(date(2026, 9, 10), ("https://x/b",), 2, 40, 0.05, 9.0),
        Fact(date(2026, 9, 10), ("https://x/c",), 0, 12, 0.0, 30.0),
    ]
    store.write("page", prev + now, replace_days={date(2026, 9, 1), date(2026, 9, 10)})
    store.write(
        "query",
        [
            Fact(date(2026, 9, 1), ("栗林公園 営業時間",), 1, 40, 0.02, 6.0),
            Fact(date(2026, 9, 10), ("栗林公園 営業時間",), 1, 60, 0.02, 6.0),
            Fact(date(2026, 9, 10), ("金刀比羅宮 今日",), 0, 20, 0.0, 15.0),
        ],
        replace_days={date(2026, 9, 1), date(2026, 9, 10)},
    )


def test_the_four_aggregations(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    _seed(store)
    urls = ["https://x/a", "https://x/b", "https://x/c", "https://x/silent"]
    s = summarise(store, site_urls=urls, days=7, today=date(2026, 9, 14))

    assert [p.page for p in s.low_ctr] == ["https://x/a"]  # 表示が多く CTR が低い
    assert [row[0] for row in s.dropped] == ["https://x/b"]  # 順位が落ちた（4.0 → 9.0）
    assert [p.page for p in s.new_pages] == ["https://x/c"]  # 新しく表示され始めた
    assert s.silent_pages == ["https://x/silent"]  # まだ表示されていない
    assert s.new_queries == ["金刀比羅宮 今日"]
    assert s.impressions == 252 and s.prev_impressions == 150


def test_a_report_with_no_data_says_so(tmp_path: Path) -> None:
    """データが 0 でも「壊れている」ではなく「まだ無い」と読める形にする。"""
    store = SearchStore(tmp_path)
    text = markdown(summarise(store, site_urls=["https://x/a"], days=7, today=date(2026, 9, 14)))
    assert "まだ表示回数がありません" in text
    assert "URL 検査: まだ 1 ページも検査していません" in text
    assert "サイトマップ: まだ Google が取得していません" in text


def test_the_report_leads_with_how_indexing_is_going(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    store.write_sitemaps(
        [
            {
                "path": "https://x/sitemap.xml",
                "lastDownloaded": "2026-09-13T08:35:39.139Z",
                "errors": "0",
                "warnings": "0",
                "contents": [{"type": "web", "submitted": "5", "indexed": "0"}],
            }
        ],
        checked_at=datetime(2026, 9, 14, 9, 0),
    )
    store.write_urls(
        {
            "https://x/a": {
                "verdict": "PASS",
                "coverage_state": "Submitted and indexed",
                "checked_on": "2026-09-14",
            },
            "https://x/b": {
                "verdict": "NEUTRAL",
                "coverage_state": "Discovered - currently not indexed",
                "checked_on": "2026-09-14",
            },
        },
        checked_at=datetime(2026, 9, 14, 9, 0),
    )
    urls = [f"https://x/{n}" for n in "abcdefgh"]
    text = markdown(summarise(store, site_urls=urls, days=7, today=date(2026, 9, 14)))
    assert "2 ページを検査して 1 ページが登録済み" in text
    # サイトマップの送信数は「Google が最後に取得した中身」。サイトの URL 数との差は待ち
    assert "サイトマップ再取得待ち" in text
    # `indexed` は Google が返さなくなった項目なので、インデックス数として出さない
    assert "5** URL" in text and "0 ページが登録済み" not in text


def test_the_report_dates_itself_in_japan_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UTC の実行機で走るので、素の today() で日付を取ると JST の朝に前日になる（ADR 0018）。

    実際、週次の Issue が「2026-09-13 時点」と出た（走ったのは JST の 9/14 朝）。
    """
    import sitemill.search.report as module

    monkeypatch.setattr(module, "jst_today", lambda: date(2026, 9, 14))
    summary = summarise(SearchStore(tmp_path), days=7)
    assert summary.end == date(2026, 9, 14)


# --- インデックスされなかった理由の内訳 ---------------------------------------


def _urls(store: SearchStore, rows: dict[str, tuple[str, str]], day: str = "2026-09-17") -> None:
    """URL → (状態, Google の選んだ正規ページ)。"""
    store.write_urls(
        {
            url: {
                "verdict": "PASS" if state == "Submitted and indexed" else "NEUTRAL",
                "coverage_state": state,
                "google_canonical": canonical,
                "last_crawl": "2026-09-15T00:00:00Z",
                "checked_on": day,
            }
            for url, (state, canonical) in rows.items()
        },
        checked_at=datetime(2026, 9, 17, 9, 0),
    )


def test_inspection_keeps_one_count_row_per_day(tmp_path: Path) -> None:
    """週の比較に使う件数は日ごとに 1 行。同じ日に取り込み直しても増えない。"""
    store = SearchStore(tmp_path)
    console = FakeConsole({}, {"https://x/a": "Submitted and indexed"})
    urls = ["https://x/a", "https://x/b"]
    inspect_urls(console, store, urls, limit=2, now=datetime(2026, 9, 17, 9, 0))
    inspect_urls(console, store, urls, limit=2, now=datetime(2026, 9, 17, 21, 0))
    history = store.read_state_history()
    assert list(history) == ["2026-09-17"]
    assert history["2026-09-17"] == {"Submitted and indexed": 1, "URL is unknown to Google": 1}


def test_the_reasons_are_listed_in_search_consoles_words_with_examples(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    _urls(
        store,
        {
            "https://x/ok": ("Submitted and indexed", ""),
            "https://x/a": ("Discovered - currently not indexed", ""),
            "https://x/b": ("Discovered - currently not indexed", ""),
            "https://x/c": ("Crawled - currently not indexed", ""),
            "https://x/d": ("Duplicate, Google chose different canonical than user", "https://x/e"),
        },
    )
    text = markdown(summarise(store, site_urls=["https://x/ok"], today=date(2026, 9, 17)))
    assert "インデックスされなかった理由" in text
    assert "| 検出 - インデックス未登録 | 2 | — |" in text
    assert "| クロール済み - インデックス未登録 | 1 | — |" in text
    assert "https://x/d → https://x/e" in text  # 重複は Google の選んだ正規ページを添える
    # 登録済みは例にしない
    assert "https://x/ok" not in text.split("インデックスされなかった理由")[1]
    assert "比較は次回から出ます" in text


def test_the_reasons_compare_with_a_week_before(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    store.write_state_counts(date(2026, 9, 9), {"Discovered - currently not indexed": 9})
    store.write_state_counts(date(2026, 9, 12), {"Discovered - currently not indexed": 5})
    _urls(store, {"https://x/a": ("Discovered - currently not indexed", "")})
    summary = summarise(store, site_urls=["https://x/a"], today=date(2026, 9, 17))
    # 7 日前（09-10）以前で一番近い日と比べる。09-12 は 1 週間経っていないので使わない
    assert summary.index.previous_day == "2026-09-09"
    assert "| 検出 - インデックス未登録 | 1 | -8 |" in markdown(summary)


def test_the_report_says_when_nothing_was_left_out(tmp_path: Path) -> None:
    store = SearchStore(tmp_path)
    _urls(store, {"https://x/a": ("Submitted and indexed", "")})
    text = markdown(summarise(store, site_urls=["https://x/a"], today=date(2026, 9, 17)))
    assert "検査したページはすべて登録済みです" in text


def test_host_variants_are_the_www_and_http_forms() -> None:
    from sitemill.search import host_variants

    assert host_variants("https://example.com") == [
        "http://example.com/",
        "https://www.example.com/",
        "http://www.example.com/",
    ]
    assert host_variants("https://www.example.com/") == [
        "http://www.example.com/",
        "https://example.com/",
        "http://example.com/",
    ]


def test_the_redirect_check_tells_expected_from_unexpected(tmp_path: Path) -> None:
    """画面の「ページにリダイレクトがあります」は www・http の形。

    転送として数えられていれば想定どおり。
    """
    from sitemill.search import inspect_variants

    store = SearchStore(tmp_path)
    console = FakeConsole(
        {},
        {
            "http://example.com/": "Submitted and indexed",
            "https://www.example.com/": "Page with redirect",
            "http://www.example.com/": "Crawled - currently not indexed",
        },
    )
    inspect_variants(console, store, "https://example.com", now=datetime(2026, 9, 17, 9, 0))
    assert set(store.read_variants()) == set(console.asked)
    _urls(store, {"https://example.com/": ("Submitted and indexed", "")})
    text = markdown(summarise(store, site_urls=["https://example.com/"], today=date(2026, 9, 17)))
    lines = {
        ln.split(":", 2)[0] + ":" + ln.split(":", 2)[1]: ln
        for ln in text.splitlines()
        if ln.startswith("- http")
    }
    assert "転送として数えられている（想定どおり）" in lines["- https://www.example.com/"]
    assert "本来の URL と同じページ" in lines["- http://example.com/"]
    assert "**転送として扱われていない" in lines["- http://www.example.com/"]
