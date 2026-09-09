"""quote-then-parse の本体。LLM の出力を引用検証と決定的パーサで FieldValue にする（ADR 0004）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sitemill.diff.normalize import page_text, squash
from sitemill.extract.llm.base import LLMProvider, LLMResult
from sitemill.extract.quotes import quote_in_source, verbatim_overlap
from sitemill.extract.spec import ExtractedItem, ExtractionSpec
from sitemill.metrics.extraction import ExtractionMetrics
from sitemill.models import FieldStatus, FieldValue

log = logging.getLogger(__name__)


@dataclass
class PageInput:
    url: str
    kind: str
    text: str
    truncated: bool = False


def prepare_input(
    html: str, *, url: str, kind: str, selector: str | None = None, max_chars: int = 60_000
) -> PageInput:
    text = page_text(html, selector)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return PageInput(url=url, kind=kind, text=text, truncated=truncated)


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
        if not quote_in_source(quote, source_squashed):
            item.fields[qf.target] = FieldValue(
                quote=quote, status=FieldStatus.quote_not_in_source, note="quote_not_in_source"
            )
            continue
        if qf.parser is None:
            item.fields[qf.target] = FieldValue(value=quote, quote=quote, status=FieldStatus.parsed)
            continue
        value, note = qf.parser(quote)
        if value is None:
            item.fields[qf.target] = FieldValue(
                quote=quote, status=FieldStatus.unparsed, note=note or "unparsed"
            )
        else:
            item.fields[qf.target] = FieldValue(
                value=value, quote=quote, status=FieldStatus.parsed, note=note
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
