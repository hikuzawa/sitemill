"""JSON-LD の営業時間を読む検査（ADR 0018 追記）。

施設が自分で書いた機械可読の宣言なので、本文より確実。ただし「組織に付いた時間」は
事務所の受付時間であり得るので、場所の型を優先することまで確かめる。
"""

from __future__ import annotations

from datetime import time

from sitemill.extract.pipeline import fill_structured_hours, prepare_input
from sitemill.extract.spec import ExtractedItem, ExtractionSpec
from sitemill.models import FieldStatus, FieldValue
from sitemill.parse.jsonld import iter_jsonld, opening_hours


def _page(*scripts: str) -> str:
    body = "".join(f'<script type="application/ld+json">{s}</script>' for s in scripts)
    return f"<html><head>{body}</head><body><p>本文に時間は書かれていない</p></body></html>"


ATTRACTION = """
{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "運営会社"},
  {"@type": "TouristAttraction", "name": "二十四の瞳映画村",
   "openingHoursSpecification": {"@type": "OpeningHoursSpecification",
     "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
     "opens": "09:00", "closes": "17:00"}}]}
"""


def test_opening_hours_come_from_the_place_not_the_company() -> None:
    periods, quote, note = opening_hours(_page(ATTRACTION))
    assert note == "jsonld_place"
    assert periods is not None
    assert len(periods) == 1
    assert periods[0].ranges[0].start == time(9, 0)
    assert periods[0].ranges[0].end == time(17, 0)
    # 全曜日は「毎日」。曜日で絞らない
    assert periods[0].days.every_day
    assert quote is not None
    assert "二十四の瞳映画村" in quote and "09:00" in quote


def test_the_graph_is_flattened_and_broken_json_is_skipped() -> None:
    nodes = iter_jsonld(_page("{ this is not json", ATTRACTION))
    assert any(n.get("name") == "二十四の瞳映画村" for n in nodes)


def test_weekday_lists_become_a_day_selector() -> None:
    page = _page(
        """
        {"@type": "Museum", "name": "館",
         "openingHoursSpecification": [
           {"dayOfWeek": ["Tuesday","Wednesday","Thursday","Friday"],
            "opens": "09:30", "closes": "17:00"},
           {"dayOfWeek": ["Saturday","Sunday","PublicHolidays"],
            "opens": "09:30", "closes": "18:00"}]}
        """
    )
    periods, _, note = opening_hours(page)
    assert note == "jsonld_place"
    assert periods is not None
    assert periods[0].days.weekdays == (1, 2, 3, 4)
    assert periods[1].days.weekdays == (5, 6)
    assert periods[1].days.include_holidays is True
    assert periods[1].ranges[0].end == time(18, 0)


def test_a_period_limited_spec_is_not_taken_as_the_regular_hours() -> None:
    """`validThrough` のある時間は期間限定。恒常の営業時間として採ると特例で上書きしてしまう。"""
    page = _page(
        """
        {"@type": "TouristAttraction", "name": "館",
         "openingHoursSpecification": {"dayOfWeek": ["Monday"], "opens": "08:00",
           "closes": "20:00", "validFrom": "2026-09-19", "validThrough": "2026-09-23"}}
        """
    )
    periods, quote, note = opening_hours(page)
    assert periods is None
    assert quote is None
    assert note == "no_opening_hours"


def test_hours_on_an_organization_are_marked_as_such() -> None:
    """組織に付いた時間は事務所の受付時間であり得る。値にはするが注記で区別する。"""
    page = _page(
        """
        {"@type": "LocalBusiness", "name": "会社",
         "openingHoursSpecification": {"dayOfWeek": ["Monday"], "opens": "08:30",
           "closes": "17:15"}}
        """
    )
    periods, _, note = opening_hours(page)
    assert note == "jsonld_organization"
    assert periods is not None
    assert periods[0].ranges[0].end == time(17, 15)


def test_unreadable_weekday_words_are_not_guessed() -> None:
    page = _page(
        """
        {"@type": "Museum", "name": "館",
         "openingHoursSpecification": {"dayOfWeek": ["毎日"], "opens": "09:00",
           "closes": "17:00"}}
        """
    )
    assert opening_hours(page) == (None, None, "no_opening_hours")


def test_half_a_range_is_not_a_range() -> None:
    page = _page('{"@type": "Museum", "openingHoursSpecification": {"opens": "09:00"}}')
    assert opening_hours(page)[0] is None


def test_the_short_string_form_is_read() -> None:
    page = _page('{"@type": "Park", "name": "園", "openingHours": "Mo-Su 07:00-17:00"}')
    periods, quote, note = opening_hours(page)
    assert note == "jsonld_place"
    assert periods is not None
    assert periods[0].days.every_day
    assert periods[0].ranges[0].start == time(7, 0)
    assert quote is not None and "Mo-Su" in quote


def test_the_short_form_wrapping_the_week_end_is_read() -> None:
    page = _page('{"@type": "Park", "openingHours": ["Sa-Mo 10:00-16:00"]}')
    periods, _, _ = opening_hours(page)
    assert periods is not None
    assert periods[0].days.weekdays == (0, 5, 6)


def test_a_page_without_jsonld_says_so() -> None:
    assert opening_hours("<html><body>時間は 9:00〜17:00</body></html>") == (
        None,
        None,
        "no_jsonld",
    )


def test_schema_org_urls_are_accepted_for_weekdays_and_types() -> None:
    page = _page(
        """
        {"@type": "https://schema.org/Museum", "name": "館",
         "openingHoursSpecification": {"dayOfWeek": ["https://schema.org/Monday"],
           "opens": "10:00", "closes": "16:00"}}
        """
    )
    periods, _, note = opening_hours(page)
    assert note == "jsonld_place"
    assert periods is not None
    assert periods[0].days.weekdays == (0,)


# --- 抽出の流れに組み込んだときの振る舞い -----------------------------------


def test_the_declaration_fills_the_field_only_when_the_body_gave_nothing() -> None:
    """本文の引用のほうが情報が多い（季節別・最終入館）。取れているならそちらを残す。"""
    spec = ExtractionSpec(
        name="spot",
        prompt_version="v1",
        system_prompt="",
        output_schema={},
        quote_fields=(),
        structured_hours_target="hours",
    )
    page = prepare_input(_page(ATTRACTION), url="https://example.jp/", kind="spot_detail")
    assert page.structured_hours is not None
    assert "openingHoursSpecification" not in page.text  # LLM には渡さない

    empty = ExtractedItem()
    fill_structured_hours(spec, page, [empty])
    assert empty.fields["hours"].status is FieldStatus.parsed
    assert empty.fields["hours"].note == "jsonld_place"
    assert "hours_from_jsonld" in empty.flags

    from_body = ExtractedItem(
        fields={
            "hours": FieldValue(
                value=[], quote="9:00〜17:00（最終入館16:30）", status=FieldStatus.parsed
            )
        }
    )
    fill_structured_hours(spec, page, [from_body])
    assert from_body.fields["hours"].quote == "9:00〜17:00（最終入館16:30）"
    assert from_body.flags == []


def test_a_spec_that_does_not_declare_it_is_untouched() -> None:
    """既存サービス（akiya-atlas）の挙動を変えない。宣言しなければ何もしない。"""
    spec = ExtractionSpec(
        name="listing", prompt_version="v1", system_prompt="", output_schema={}, quote_fields=()
    )
    page = prepare_input(_page(ATTRACTION), url="https://example.jp/", kind="listing_detail")
    item = ExtractedItem()
    fill_structured_hours(spec, page, [item])
    assert item.fields == {}
