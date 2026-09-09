"""抽出精度の集計。項目別の抽出率と null 理由を数え、eval と本番で同じ形を使う（ADR 0010）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from sitemill.models import FieldStatus

if TYPE_CHECKING:
    from sitemill.extract.spec import ExtractedItem


class FieldMetrics(BaseModel):
    total: int = 0
    parsed: int = 0
    not_found: int = 0
    unparsed: int = 0
    quote_not_in_source: int = 0
    notes: dict[str, int] = Field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.parsed / self.total if self.total else 0.0

    def merge(self, other: FieldMetrics) -> None:
        self.total += other.total
        self.parsed += other.parsed
        self.not_found += other.not_found
        self.unparsed += other.unparsed
        self.quote_not_in_source += other.quote_not_in_source
        for k, v in other.notes.items():
            self.notes[k] = self.notes.get(k, 0) + v


class ExtractionMetrics(BaseModel):
    pages: int = 0
    truncated_pages: int = 0
    items: int = 0
    summary_replaced: int = 0
    flags: dict[str, int] = Field(default_factory=dict)
    fields: dict[str, FieldMetrics] = Field(default_factory=dict)

    def observe(self, item: ExtractedItem) -> None:
        self.items += 1
        for flag in item.flags:
            self.flags[flag] = self.flags.get(flag, 0) + 1
        if "summary_verbatim_overlap" in item.flags:
            self.summary_replaced += 1
        for name, fv in item.fields.items():
            fm = self.fields.setdefault(name, FieldMetrics())
            fm.total += 1
            if fv.status is FieldStatus.parsed:
                fm.parsed += 1
            elif fv.status is FieldStatus.not_found:
                fm.not_found += 1
            elif fv.status is FieldStatus.unparsed:
                fm.unparsed += 1
            else:
                fm.quote_not_in_source += 1
            if fv.note:
                fm.notes[fv.note] = fm.notes.get(fv.note, 0) + 1

    def merge(self, other: ExtractionMetrics) -> None:
        self.pages += other.pages
        self.truncated_pages += other.truncated_pages
        self.items += other.items
        self.summary_replaced += other.summary_replaced
        for k, v in other.flags.items():
            self.flags[k] = self.flags.get(k, 0) + v
        for name, fm in other.fields.items():
            self.fields.setdefault(name, FieldMetrics()).merge(fm)

    def rates(self) -> dict[str, float]:
        return {name: round(fm.rate, 3) for name, fm in sorted(self.fields.items())}

    def table(self) -> str:
        lines = [f"pages={self.pages} items={self.items} truncated={self.truncated_pages}"]
        lines.append(
            f"summary_replaced={self.summary_replaced} flags={dict(sorted(self.flags.items()))}"
        )
        lines.append(f"{'field':<16}{'rate':>6}{'parsed':>8}{'nf':>5}{'unp':>5}{'qns':>5}  notes")
        for name, fm in sorted(self.fields.items()):
            notes = ", ".join(f"{k}={v}" for k, v in sorted(fm.notes.items()))
            lines.append(
                f"{name:<16}{fm.rate:>6.2f}{fm.parsed:>8}{fm.not_found:>5}{fm.unparsed:>5}"
                f"{fm.quote_not_in_source:>5}  {notes}"
            )
        return "\n".join(lines)
