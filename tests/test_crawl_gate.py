"""巡回ゲートの二段化とページ種別の一般化（ADR 0017）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sitemill.models import (
    DEFAULT_CRAWLABLE_OPERATORS,
    OFFICIAL_OPERATORS,
    CrawlPolicy,
    FollowRule,
    OperatorEvidence,
    OperatorKind,
    PageKind,
    SeedPage,
    Source,
)
from sitemill.service import check_crawl_gate, crawlable_operator_kinds


def _source(**overrides: object) -> Source:
    base: dict[str, object] = {
        "id": "x",
        "name": "例",
        "operator": "例",
        "operator_kind": OperatorKind.municipality,
        "operator_evidence": OperatorEvidence(quote="運営: 例", url="https://example.com/"),
        "policy": CrawlPolicy.crawl,
        "official_url": "https://example.com/",
        "pages": [SeedPage(url="https://example.com/a/")],
    }
    base.update(overrides)
    return Source.model_validate(base)


class _Service:
    id = "svc"


class _TourismService:
    id = "tourism"
    crawlable_operator_kinds = (
        OperatorKind.municipality,
        OperatorKind.prefecture,
        OperatorKind.facility_official,
        OperatorKind.transport_operator,
    )


# --- Source 単体の検証（エンジン側の下限）---------------------------------


def test_official_operators_can_be_crawled_with_evidence() -> None:
    for kind in OFFICIAL_OPERATORS:
        assert _source(operator_kind=kind).crawlable


def test_third_party_and_unknown_can_never_be_crawled() -> None:
    for kind in (OperatorKind.third_party, OperatorKind.unknown):
        with pytest.raises(ValidationError, match="運営主体"):
            _source(operator_kind=kind)


def test_crawl_still_needs_evidence_and_pages() -> None:
    with pytest.raises(ValidationError, match="根拠"):
        _source(operator_kind=OperatorKind.facility_official, operator_evidence=None)
    with pytest.raises(ValidationError, match="pages"):
        _source(operator_kind=OperatorKind.transport_operator, pages=[])


# --- サービスごとの宣言（二段目）-------------------------------------------


def test_service_without_a_declaration_stays_municipality_only() -> None:
    """既定は現行のまま。akiya-atlas はこの経路で自治体だけに絞られ続ける。"""
    assert crawlable_operator_kinds(_Service()) == DEFAULT_CRAWLABLE_OPERATORS
    check_crawl_gate(_Service(), [_source(operator_kind=OperatorKind.municipality)])
    with pytest.raises(ValueError, match="巡回してよい運営主体"):
        check_crawl_gate(_Service(), [_source(operator_kind=OperatorKind.facility_official)])


def test_service_can_widen_the_gate_within_the_official_kinds() -> None:
    svc = _TourismService()
    assert OperatorKind.facility_official in crawlable_operator_kinds(svc)
    check_crawl_gate(
        svc,
        [
            _source(id="a", operator_kind=OperatorKind.facility_official),
            _source(id="b", operator_kind=OperatorKind.transport_operator),
        ],
    )
    # 宣言していない種別（観光協会）は、Source 単体では通っても巡回させない
    with pytest.raises(ValueError, match="巡回してよい運営主体"):
        check_crawl_gate(svc, [_source(operator_kind=OperatorKind.tourism_association)])


def test_service_cannot_declare_third_party() -> None:
    class Bad:
        id = "bad"
        crawlable_operator_kinds = (OperatorKind.third_party,)

    with pytest.raises(ValueError, match="third_party"):
        crawlable_operator_kinds(Bad())


def test_service_cannot_declare_an_unknown_kind() -> None:
    class Bad:
        id = "bad"
        crawlable_operator_kinds = ("shrine",)

    with pytest.raises(ValueError, match="未知の運営主体"):
        crawlable_operator_kinds(Bad())


def test_link_only_sources_are_not_gated() -> None:
    """巡回しない source は運営主体が何であってもよい（リンクとして案内するだけ）。"""
    link_only = _source(policy=CrawlPolicy.link_only, operator_kind=OperatorKind.third_party)
    check_crawl_gate(_Service(), [link_only])


# --- ページ種別 -------------------------------------------------------------


def test_services_can_use_their_own_page_kinds() -> None:
    page = SeedPage(url="https://example.com/hours/", kind="spot_hours")
    rule = FollowRule(pattern=r"/notice/\d+", kind="notice")
    assert page.kind == "spot_hours"
    assert rule.kind == "notice"


def test_common_page_kinds_still_work() -> None:
    page = SeedPage(url="https://example.com/", kind=PageKind.listing_index)
    assert page.kind == "listing_index"
    assert PageKind.notice == "notice"


def test_page_kind_keeps_the_old_dot_value_access() -> None:
    """種別を Enum から str に緩めたときの互換（ADR 0017）。

    `kind.value` と書かれた既存の利用者を壊さない。
    """
    page = SeedPage(url="https://example.com/", kind="spot_hours")
    assert page.kind.value == "spot_hours"
    assert SeedPage(url="https://example.com/").kind.value == "info"
    assert FollowRule(pattern="x").kind.value == "listing_detail"
    assert page.model_dump(mode="json")["kind"] == "spot_hours"


def test_page_kind_typos_are_rejected() -> None:
    """種別は状態ファイルと抽出仕様の引き当てに使う。表記ゆれを黙って通さない。"""
    for bad in ("Listing Index", "listing-index", "1st", ""):
        with pytest.raises(ValidationError):
            SeedPage(url="https://example.com/", kind=bad)


def test_pending_is_a_third_state_and_never_crawled() -> None:
    """運営主体を判定できていないものは pending。link_only と混ぜない（ADR 0011）。"""
    pending = _source(policy=CrawlPolicy.pending, operator_kind=OperatorKind.unknown)
    assert not pending.crawlable
    assert pending.policy is not CrawlPolicy.link_only
    # 根拠が無くても pending なら作れる（判定できていないことを記録するための状態）
    assert _source(policy=CrawlPolicy.pending, operator_evidence=None, pages=[]) is not None
    check_crawl_gate(_Service(), [pending])  # 巡回しないのでゲートには掛からない
