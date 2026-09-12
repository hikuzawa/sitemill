from pathlib import Path

from sitemill.diff.state import CrawlState
from sitemill.store import RawCache, read_jsonl, write_jsonl


def test_state_roundtrip_sorted(tmp_path: Path) -> None:
    st = CrawlState()
    st.get_or_create("https://b.example/2", "s", "listing_detail")
    st.get_or_create("https://a.example/1", "s", "listing_index").pending_extract = True
    path = tmp_path / "crawl.json"
    st.save(path)
    text = path.read_text(encoding="utf-8")
    assert text.index("https://a.example/1") < text.index("https://b.example/2")
    loaded = CrawlState.load(path)
    assert [s.url for s in loaded.pending("s")] == ["https://a.example/1"]
    assert loaded.for_source("s")[1].kind == "listing_detail"
    assert loaded.pending("other") == []


def test_raw_cache_roundtrip(tmp_path: Path) -> None:
    raw = RawCache(tmp_path)
    content = "<p>空き家</p>".encode("cp932")
    raw.save(
        "s", "https://a.example/x", content, {"encoding": "cp932", "content_type": "text/html"}
    )
    loaded = raw.load("s", "https://a.example/x")
    assert loaded is not None
    assert loaded[0] == content
    assert loaded[1]["url"] == "https://a.example/x"
    assert raw.load_text("s", "https://a.example/x") == "<p>空き家</p>"
    assert raw.load("s", "https://a.example/none") is None


def test_jsonl_roundtrip_keeps_japanese(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    rows = [{"b": "東御市", "a": 1}, {"a": 2, "b": "佐久市"}]
    assert write_jsonl(path, rows) == 2
    text = path.read_text(encoding="utf-8")
    assert '{"a": 1, "b": "東御市"}' in text
    assert read_jsonl(path) == rows


def test_json_handles_times_as_well_as_dates() -> None:
    """開館時間の規則は time を持つ（ADR 0018）。date だけ扱えると保存時に落ちる。"""
    from datetime import date, time

    from sitemill.store.jsonio import dumps

    assert '"10:00:00"' in dumps({"open": time(10, 0)})
    assert '"2026-09-12"' in dumps({"day": date(2026, 9, 12)})
