from pathlib import Path

from sitemill.review import ReviewCandidate, ReviewQueue, render_review_table


def _cand(**kw) -> ReviewCandidate:
    base = dict(key="202193", label="長野県 東御市", confidence=0.4, proposed_policy="pending")
    base.update(kw)
    return ReviewCandidate(**base)


def test_pending_vs_adopted() -> None:
    q = ReviewQueue(
        prefecture="長野県",
        prefecture_slug="nagano",
        created_at="2026-09-10T00:00:00Z",
        candidates=[
            _cand(),
            _cand(key="202177", label="長野県 佐久市", proposed_policy="crawl"),
        ],
    )
    assert [c.key for c in q.pending()] == ["202193"]
    assert [c.key for c in q.auto_adopted()] == ["202177"]


def test_roundtrip_yaml(tmp_path: Path) -> None:
    q = ReviewQueue(
        prefecture="長野県",
        prefecture_slug="nagano",
        created_at="2026-09-10T00:00:00Z",
        candidates=[_cand(url="https://x.example/akiya/", evidence_quote="運営: 東御市")],
    )
    path = tmp_path / "nagano.yaml"
    q.save(path)
    text = path.read_text(encoding="utf-8")
    assert "東御市" in text and "運営" in text
    loaded = ReviewQueue.load(path)
    assert loaded.candidates[0].url == "https://x.example/akiya/"


def test_render_table() -> None:
    table = render_review_table(
        [
            _cand(
                url="https://www.ina-akiyabank.jp/",
                page_class="spa",
                class_label="JavaScript 描画（未対応）",
                confidence=0.75,
                reason="SPA のため静的HTMLに物件が無い",
                proposed_action="却下",
            )
        ]
    )
    assert "| # | 対象 |" in table and "東御市" in table and "却下" in table
    assert "JavaScript" in table
    assert render_review_table([]).startswith("（レビュー待ち")
