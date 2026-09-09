import pytest
from pydantic import ValidationError

from sitemill.models import (
    CrawlPolicy,
    FieldStatus,
    FieldValue,
    LicenseId,
    LicenseVerdict,
    OperatorEvidence,
    OperatorKind,
    PageMeta,
    SeedPage,
    Source,
)


def test_field_value_defaults_to_not_found() -> None:
    fv: FieldValue[int] = FieldValue()
    assert fv.status is FieldStatus.not_found
    assert not fv.ok
    ok: FieldValue[int] = FieldValue(value=1, quote="1円", status=FieldStatus.parsed)
    assert ok.ok


def test_license_verdict_default_denied() -> None:
    v = LicenseVerdict.denied("no explicit license", url="https://example.com/")
    assert not v.allowed and v.license_id is None
    assert "不採用" in v.label
    g = LicenseVerdict.granted(
        LicenseId.CC_BY_4_0,
        evidence_url="https://example.com/terms",
        evidence_text="CC BY 4.0",
        credit_text="出典: 例市",
    )
    assert g.allowed and "CC BY 4.0" in g.label


def _source(**overrides: object) -> Source:
    base: dict[str, object] = {
        "id": "x",
        "name": "例市",
        "operator": "例市",
        "operator_kind": OperatorKind.municipality,
        "operator_evidence": OperatorEvidence(quote="運営: 例市", url="https://example.com/"),
        "policy": CrawlPolicy.crawl,
        "official_url": "https://example.com/",
        "pages": [SeedPage(url="https://example.com/akiya/")],
    }
    base.update(overrides)
    return Source.model_validate(base)


def test_source_crawl_requires_public_operator() -> None:
    assert _source().crawlable
    with pytest.raises(ValidationError, match="運営主体"):
        _source(operator_kind=OperatorKind.third_party)
    with pytest.raises(ValidationError, match="根拠"):
        _source(operator_evidence=None)
    with pytest.raises(ValidationError, match="pages"):
        _source(pages=[])
    link_only = _source(policy=CrawlPolicy.link_only, operator_kind=OperatorKind.third_party)
    assert not link_only.crawlable


def test_page_meta_url_path() -> None:
    assert (
        PageMeta(title="t", path="nagano/20219-tomi/index.html").url_path == "/nagano/20219-tomi/"
    )
    assert PageMeta(title="t", path="sitemap.xml").url_path == "/sitemap.xml"
