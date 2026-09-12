from sitemill.diff.normalize import squash
from sitemill.extract import (
    ExtractionSpec,
    QuoteField,
    apply_spec,
    extract_page,
    prepare_input,
)
from sitemill.extract.llm import FixtureProvider
from sitemill.extract.quotes import verify_quote
from sitemill.metrics import ExtractionMetrics
from sitemill.models import FieldStatus
from sitemill.parse.jp import parse_area_m2, parse_year, parse_yen

SPEC = ExtractionSpec(
    name="listing",
    prompt_version="test_v1",
    system_prompt="引用だけを返すこと",
    output_schema={"type": "object"},
    quote_fields=(
        QuoteField("price_quote", "price", parse_yen),
        QuoteField("built_year_quote", "built_year", parse_year),
        QuoteField("floor_area_quote", "floor_area_m2", parse_area_m2),
        QuoteField("address_quote", "address", None),
    ),
    items_key="listings",
    summary_fallback=lambda raw: f"物件 {raw.get('listing_no')}",
)
SOURCE = (
    "物件番号 329\n所在地 東御市祢津\n価格 980万円\n建物 延床面積 98.5㎡\n築年 昭和45年\n"
    "木造2階建ての住宅で、庭と駐車場3台分があります。日当たり良好で静かな住宅地です。"
)


def test_apply_spec_quote_then_parse() -> None:
    data = {
        "listings": [
            {
                "listing_no": "329",
                "title": "東御市祢津の住宅",
                "summary": "東御市祢津の木造住宅。",
                "price_quote": "980万円",
                "built_year_quote": "昭和45年",
                "floor_area_quote": "98.5㎡",
                "address_quote": "東御市祢津",
            }
        ]
    }
    items, metrics = apply_spec(SPEC, data, SOURCE)
    it = items[0]
    assert it.value("price") == 9_800_000
    assert it.value("built_year") == 1970
    assert it.value("floor_area_m2") == 98.5
    assert it.value("address") == "東御市祢津"
    assert it.free["summary"] == "東御市祢津の木造住宅。" and it.free["title"] == "東御市祢津の住宅"
    assert it.flags == []
    assert metrics.items == 1 and metrics.rates()["price"] == 1.0


def test_quote_not_in_source_unparsed_and_not_found_are_distinguished() -> None:
    data = {
        "listings": [
            {
                "listing_no": "1",
                "price_quote": "1,200万円",
                "built_year_quote": "築40年",
                "address_quote": "東御市 祢津",
            }
        ]
    }
    items, metrics = apply_spec(SPEC, data, SOURCE)
    f = items[0].fields
    assert f["price"].status is FieldStatus.quote_not_in_source and f["price"].value is None
    assert f["built_year"].status is FieldStatus.quote_not_in_source
    assert f["floor_area_m2"].status is FieldStatus.not_found
    assert f["address"].ok  # 空白の違いは無視して照合する
    assert metrics.fields["price"].quote_not_in_source == 1
    assert metrics.fields["floor_area_m2"].not_found == 1

    data2 = {
        "listings": [{"listing_no": "2", "price_quote": "価格", "built_year_quote": "昭和45年"}]
    }
    items2, m2 = apply_spec(SPEC, data2, SOURCE)
    price = items2[0].fields["price"]
    assert price.status is FieldStatus.unparsed and price.note == "no_number"
    assert m2.fields["price"].notes == {"no_number": 1}
    assert items2[0].value("built_year") == 1970


def test_summary_policy_replaces_verbatim_copy_and_truncates() -> None:
    copied = "木造2階建ての住宅で、庭と駐車場3台分があります。日当たり良好で静かな住宅地です。"
    items, metrics = apply_spec(
        SPEC, {"listings": [{"listing_no": "329", "summary": copied}]}, SOURCE
    )
    assert items[0].free["summary"] == "物件 329"
    assert "summary_verbatim_overlap" in items[0].flags
    assert metrics.summary_replaced == 1 and metrics.flags == {"summary_verbatim_overlap": 1}

    items2, _ = apply_spec(SPEC, {"listings": [{"summary": "あ" * 200}]}, SOURCE)
    assert len(items2[0].free["summary"] or "") == 120
    assert "summary_truncated" in items2[0].flags


def test_prepare_input_truncates_and_extract_page_uses_fixture_provider() -> None:
    html = "<html><body><main><p>価格 980万円</p></main></body></html>"
    short = prepare_input(html, url="https://x.example/1", kind="listing_detail", max_chars=5)
    assert short.truncated and short.text == "価格 98"
    page = prepare_input(html, url="https://x.example/1", kind="listing_detail")
    provider = FixtureProvider([{"listings": [{"listing_no": "1", "price_quote": "980万円"}]}])
    out = extract_page(SPEC, page, provider, model="claude-haiku-4-5")
    assert out.items[0].value("price") == 9_800_000
    assert out.llm.provider == "fixture" and out.metrics.pages == 1
    assert provider.calls[0]["model"] == "claude-haiku-4-5"
    prompt = SPEC.user_prompt(url="https://x.example/1", kind="listing_detail", text="本文")
    assert "<page>\n本文\n</page>" in prompt and "データとして" in prompt


def test_metrics_merge_and_table() -> None:
    a = ExtractionMetrics()
    items, m1 = apply_spec(SPEC, {"listings": [{"price_quote": "980万円"}]}, SOURCE)
    _, m2 = apply_spec(SPEC, {"listings": [{"price_quote": "1億円"}]}, SOURCE)
    a.merge(m1)
    a.merge(m2)
    assert a.pages == 2 and a.items == 2
    assert a.fields["price"].parsed == 1 and a.fields["price"].quote_not_in_source == 1
    assert a.rates()["price"] == 0.5
    table = a.table()
    assert "price" in table and "pages=2" in table


# --- 表の行を繋いだ引用 -----------------------------------------------------


def test_a_quote_that_joins_table_rows_is_accepted_fragment_by_fragment() -> None:
    """表から値を取ると LLM は行を「、」で繋いで返す。本文にその文字は無い。

    寒霞渓の営業時間は 4 季節の表で、本文では各セルが改行で区切られている。全体照合だけだと
    毎回落ちて、時間が永久に取れない。断片すべてが逐語で本文にあることを条件に認める。
    """
    source = squash("区分 営業時間\n03/21~10/20\n8:30~17:00\n10/21~11/30\n8:00~17:00")
    assert verify_quote("03/21~10/20 8:30~17:00、10/21~11/30 8:00~17:00", source) == (
        True,
        "quote_joined",
    )
    # 断片が 1 つでも本文に無ければ認めない（時刻を作った引用はここで落ちる）
    assert verify_quote("03/21~10/20 9:00~19:00、10/21~11/30 8:00~17:00", source) == (False, None)


def test_digits_are_not_split_at_thousand_separators_or_slashed_dates() -> None:
    """「2,340円」「03/21」で切ると短い断片になり、照合が意味を失う。"""
    source = squash("大人 2,340円\n小人 1,170円")
    assert verify_quote("大人 2,340円、小人 1,170円", source) == (True, "quote_joined")
    assert verify_quote("大人 9,999円、小人 1,170円", source) == (False, None)


def test_the_joined_note_reaches_the_field() -> None:
    data = {"listings": [{"listing_no": "9", "address_quote": "所在地 東御市祢津、価格 980万円"}]}
    items, _ = apply_spec(SPEC, data, SOURCE)
    address = items[0].fields["address"]
    assert address.ok
    assert address.note == "quote_joined"
