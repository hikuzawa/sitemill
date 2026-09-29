"""出どころのページを読めた時刻で鮮度を進める（提案 L、ADR 0028）。外部アクセスはしない。

障害は「ある日、全部」の形で出るので、「読めていれば進む」と「読めていなければ進まない」の両方を当てる。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sitemill.diff.freshness import mark_read, page_read_at, read_at
from sitemill.diff.state import CrawlState, UrlState

READ = datetime(2026, 9, 28, 22, 46, tzinfo=UTC)  # JST では 9/29 07:46
OLDER = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)


def page(url: str, **kw: object) -> UrlState:
    base: dict[str, object] = {
        "url": url,
        "source_id": "s",
        "fetched_at": READ,
        "content_hash": "h1",
        "extracted_hash": "h1",
    }
    base.update(kw)
    return UrlState.model_validate(base)


def state(*pages: UrlState) -> CrawlState:
    return CrawlState(urls={p.url: p for p in pages})


def test_an_unchanged_page_that_was_read_counts() -> None:
    assert page_read_at(page("a")) == READ


def test_a_failed_a_changed_or_an_unknown_page_does_not() -> None:
    assert page_read_at(page("a", error="HTTP 500")) is None
    assert page_read_at(page("a", fetched_at=None)) is None
    # 中身が変わったのに抽出がまだ（または失敗）。古い事実を新しく扱わない
    assert page_read_at(page("a", content_hash="h2")) is None
    assert page_read_at(page("a", extracted_hash=None)) is None
    assert page_read_at(None) is None  # 巡回先から外れた


def test_read_at_needs_every_source_page_and_takes_the_oldest() -> None:
    st = state(page("a"), page("b", fetched_at=OLDER))
    assert read_at(st, ["a", "b"]) == OLDER
    assert read_at(st, ["a", "gone"]) is None
    assert read_at(st, []) is None


def test_mark_read_moves_only_what_was_read_and_never_backwards() -> None:
    st = state(page("a"), page("b", error="timed out"))
    records = [
        {"url": "a", "checked_on": "2026-09-12"},
        {"url": "b", "checked_on": "2026-09-12"},  # 読めていない → 据え置き
        {"url": "a", "checked_on": "2026-10-01"},  # 前に戻さない
        {"url": "a"},  # 項目が無ければ書く
    ]
    moved = mark_read(records, st, source_url=lambda r: r["url"], field="checked_on", as_date=True)
    assert moved == 2
    assert [r.get("checked_on") for r in records] == [
        "2026-09-29",  # JST の日付（UTC だと 9/28 になる）
        "2026-09-12",
        "2026-10-01",
        "2026-09-29",
    ]


def test_mark_read_with_timestamps() -> None:
    st = state(page("a"))
    records = [{"urls": ["a"], "last_seen_at": "2026-09-20T00:00:00+00:00"}]
    assert mark_read(records, st, source_url=lambda r: r["urls"], field="last_seen_at") == 1
    assert records[0]["last_seen_at"] == READ.isoformat()
