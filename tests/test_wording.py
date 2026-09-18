"""値が同じなら言い回しを凍結する（`sitemill.store.wording`）。"""

from __future__ import annotations

from sitemill.store import freeze_wording, same_facts


def test_same_value_keeps_the_previous_quote() -> None:
    """引用の頭の「*」が消えただけなら、前回の引用を残す（実データで起きた形）。"""
    old = {"value": 0, "status": "parsed", "quote": "*さぬき市在住の小学生以下の方は無料です。"}
    new = {"value": 0, "status": "parsed", "quote": "さぬき市在住の小学生以下の方は無料です。"}
    assert freeze_wording(old, new) == old


def test_a_changed_value_takes_the_new_wording() -> None:
    old = {"value": 1000, "quote": "一般 1,000円"}
    new = {"value": 1200, "quote": "一般 1,200円"}
    assert freeze_wording(old, new) == new


def test_freshness_is_always_the_new_one() -> None:
    """取得日時は毎回動くのが正しい。値が同じでも新しい方を採る。"""
    old = {"value": 1000, "quote": "一般 1,000円", "fetched_at": "2026-09-17T00:00:00Z"}
    new = {"value": 1000, "quote": "一般 1000円", "fetched_at": "2026-09-19T00:00:00Z"}
    assert freeze_wording(old, new) == {
        "value": 1000,
        "quote": "一般 1,000円",
        "fetched_at": "2026-09-19T00:00:00Z",
    }


def test_nested_lists_are_matched_when_the_length_is_the_same() -> None:
    old = {
        "closures": [
            {
                "kind": "weekly",
                "weekdays": [2],
                "label": "*毎週水曜定休",
                "evidence": {"quote": "*毎週水曜定休"},
            }
        ]
    }
    new = {
        "closures": [
            {
                "kind": "weekly",
                "weekdays": [2],
                "label": "毎週水曜定休",
                "evidence": {"quote": "毎週水曜定休"},
            }
        ]
    }
    assert freeze_wording(old, new) == old


def test_more_items_takes_the_new_one() -> None:
    """要素が増えたら事実が変わっている。並びごと新しい方を採る。"""
    old = {"fees": [{"category": "adult", "amount": {"value": 1000, "quote": "大人 1,000円"}}]}
    new = {
        "fees": [
            {"category": "adult", "amount": {"value": 1000, "quote": "大人 1000円"}},
            {"category": "child", "amount": {"value": 500, "quote": "小人 500円"}},
        ]
    }
    assert freeze_wording(old, new) == new


def test_same_facts_ignores_wording_and_freshness() -> None:
    assert same_facts(
        {"value": 3, "quote": "3 件", "fetched_at": "a"},
        {"value": 3, "quote": "三件", "fetched_at": "b"},
    )
    assert not same_facts({"value": 3, "quote": "3 件"}, {"value": 4, "quote": "3 件"})


def test_a_missing_field_does_not_invent_wording() -> None:
    """前回に無かった項目は、そのまま新しい方を採る。"""
    old = {"value": 5}
    new = {"value": 5, "quote": "5 件"}
    assert freeze_wording(old, new) == new
