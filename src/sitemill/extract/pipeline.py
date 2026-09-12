"""quote-then-parse の本体。LLM の出力を引用検証と決定的パーサで FieldValue にする（ADR 0004）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sitemill.diff.normalize import page_text, squash
from sitemill.extract.llm.base import LLMProvider, LLMResult
from sitemill.extract.quotes import verbatim_overlap, verify_quote
from sitemill.extract.spec import ExtractedItem, ExtractionSpec
from sitemill.metrics.extraction import ExtractionMetrics
from sitemill.models import FieldStatus, FieldValue
from sitemill.models.schedule import HoursPeriod
from sitemill.parse.jsonld import opening_hours

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredHours:
    """ページが機械可読の形で宣言している営業時間（JSON-LD）。

    本文に書かれていない施設が JSON-LD だけに書いていることがある。`quote` は根拠として
    表示する原文（ここでは JSON-LD の該当部分）。
    """

    periods: list[HoursPeriod]
    quote: str
    note: str


@dataclass
class PageInput:
    url: str
    kind: str
    text: str
    truncated: bool = False
    # 本文（text）には入らない機械可読の宣言。LLM には渡さず、決定的に値にする
    structured_hours: StructuredHours | None = None


def prepare_input(
    html: str, *, url: str, kind: str, selector: str | None = None, max_chars: int = 60_000
) -> PageInput:
    text = page_text(html, selector)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    periods, quote, note = opening_hours(html)
    structured = (
        StructuredHours(periods=periods, quote=quote, note=note or "jsonld")
        if periods and quote
        else None
    )
    return PageInput(
        url=url, kind=kind, text=text, truncated=truncated, structured_hours=structured
    )


def _as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def apply_item(spec: ExtractionSpec, raw: dict[str, Any], source_squashed: str) -> ExtractedItem:
    """LLM が返した 1 件を検証・パースする。"""
    item = ExtractedItem(raw=raw)
    for qf in spec.quote_fields:
        quote = _as_str(raw.get(qf.quote_field))
        if quote is None:
            item.fields[qf.target] = FieldValue(status=FieldStatus.not_found)
            continue
        found, join_note = verify_quote(quote, source_squashed)
        if not found:
            item.fields[qf.target] = FieldValue(
                quote=quote, status=FieldStatus.quote_not_in_source, note="quote_not_in_source"
            )
            continue
        if qf.parser is None:
            item.fields[qf.target] = FieldValue(
                value=quote, quote=quote, status=FieldStatus.parsed, note=join_note
            )
            continue
        value, note = qf.parser(quote)
        if value is None:
            item.fields[qf.target] = FieldValue(
                quote=quote, status=FieldStatus.unparsed, note=note or "unparsed"
            )
        else:
            item.fields[qf.target] = FieldValue(
                value=value, quote=quote, status=FieldStatus.parsed, note=note or join_note
            )

    for name in spec.free_text_fields:
        item.free[name] = _as_str(raw.get(name))

    if spec.summary_field:
        summary = item.free.get(spec.summary_field)
        if summary is not None:
            replaced = False
            if len(summary) > spec.summary_max_chars:
                summary = summary[: spec.summary_max_chars]
                item.flags.append("summary_truncated")
            if verbatim_overlap(summary, source_squashed, spec.verbatim_overlap_chars):
                item.flags.append("summary_verbatim_overlap")
                replaced = True
            if replaced:
                summary = spec.summary_fallback(raw) if spec.summary_fallback else None
            item.free[spec.summary_field] = summary
    return item


def apply_spec(
    spec: ExtractionSpec, data: dict[str, Any], source_text: str
) -> tuple[list[ExtractedItem], ExtractionMetrics]:
    """LLM 出力全体に仕様を適用し、抽出結果と項目別の集計を返す。"""
    source_squashed = squash(source_text)
    raw_items: list[Any]
    if spec.items_key is None:
        raw_items = [data]
    else:
        raw_items = data.get(spec.items_key) or []
        if not isinstance(raw_items, list):
            raw_items = []
    metrics = ExtractionMetrics(pages=1)
    items: list[ExtractedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = apply_item(spec, raw, source_squashed)
        metrics.observe(item)
        items.append(item)
    return items, metrics


@dataclass
class PageExtraction:
    items: list[ExtractedItem]
    metrics: ExtractionMetrics
    llm: LLMResult


def extract_page(
    spec: ExtractionSpec,
    page: PageInput,
    provider: LLMProvider,
    *,
    model: str,
    max_tokens: int = 16_000,
    temperature: float | None = 0.0,
) -> PageExtraction:
    """1 ページ分を LLM に渡し、quote-then-parse を通した結果を返す。"""
    result = provider.complete_json(
        system=spec.system_prompt,
        user=spec.user_prompt(url=page.url, kind=page.kind, text=page.text),
        schema=spec.output_schema,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    items, metrics = apply_spec(spec, result.data, page.text)
    fill_structured_hours(spec, page, items)
    if page.truncated:
        metrics.truncated_pages = 1
    log.info(
        "抽出 %s: %d 件 (provider=%s model=%s cached=%s)",
        page.url,
        len(items),
        result.provider,
        result.model,
        result.cached,
    )
    return PageExtraction(items=items, metrics=metrics, llm=result)


def fill_structured_hours(
    spec: ExtractionSpec, page: PageInput, items: list[ExtractedItem]
) -> None:
    """本文から営業時間が取れなかったとき、JSON-LD の宣言で埋める。

    本文の引用のほうが季節別・最終入館まで書かれていて情報が多いので、**本文が取れていれば
    そちらを使う**。JSON-LD は本文に書いていない施設のための最後の一手である。

    LLM を通さない。JSON-LD は施設自身が書いた機械可読の宣言なので、読み取りは決定的に行う
    （ADR 0004 の「数値は決定的パーサだけが決める」に沿う）。
    """
    target = spec.structured_hours_target
    if target is None or page.structured_hours is None or not items:
        return
    item = items[0]  # 施設ページは 1 ページ 1 件（告知ページはこの設定を持たない）
    current = item.fields.get(target)
    if current is not None and current.ok:
        return
    structured = page.structured_hours
    item.fields[target] = FieldValue(
        value=structured.periods,
        quote=structured.quote,
        status=FieldStatus.parsed,
        note=structured.note,
    )
    item.flags.append(f"{target}_from_jsonld")
    log.info("%s: 営業時間を JSON-LD から取った（%s）", page.url, structured.note)
