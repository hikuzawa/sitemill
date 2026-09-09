import pytest

from sitemill.license import detect_license
from sitemill.models import LicenseId


def _page(body: str, footer: str = "") -> str:
    return f"<html><body><main>{body}</main><footer>{footer}</footer></body></html>"


def test_cc_by_4_link_is_granted_with_credit() -> None:
    html = _page(
        '<p>本サイトのデータは<a href="https://creativecommons.org/licenses/by/4.0/deed.ja">'
        "CC BY 4.0</a>で提供します</p>"
    )
    v = detect_license(html, "https://city.example/opendata", credit_name="例市")
    assert v.allowed and v.license_id is LicenseId.CC_BY_4_0
    assert v.credit_text is not None and "例市" in v.credit_text and "CC BY 4.0" in v.credit_text
    assert v.evidence_url == "https://city.example/opendata"
    assert v.evidence_text is not None and "creativecommons.org" in v.evidence_text


@pytest.mark.parametrize(
    ("text", "license_id"),
    [
        (
            "当サイトのコンテンツは政府標準利用規約（第2.0版）に従って利用できます。",
            LicenseId.GSTU_2_0,
        ),
        ("このデータは CC0 1.0 で提供します。", LicenseId.CC0_1_0),
        (
            "クリエイティブ・コモンズ 表示 4.0 国際ライセンスの下に提供されています。",
            LicenseId.CC_BY_4_0,
        ),
    ],
)
def test_text_markers_are_granted(text: str, license_id: LicenseId) -> None:
    v = detect_license(_page(f"<p>{text}</p>"), "https://x.example/", credit_name="例市")
    assert v.allowed and v.license_id is license_id


def test_restrictive_variants_are_denied() -> None:
    nc = _page('<a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC</a>')
    v = detect_license(nc, "https://x.example/", credit_name="例市")
    assert not v.allowed and "制限" in v.reason


def test_mixed_statements_are_denied() -> None:
    html = _page(
        '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>',
        footer="Copyright (c) 例市 All Rights Reserved.",
    )
    v = detect_license(html, "https://x.example/", credit_name="例市")
    assert not v.allowed and "混在" in v.reason


def test_no_statement_is_denied_by_default() -> None:
    v = detect_license(
        _page("<p>空き家バンクのご案内</p>"), "https://x.example/", credit_name="例市"
    )
    assert not v.allowed and v.license_id is None and "見つからない" in v.reason
