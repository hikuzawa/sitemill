"""実行レポート。取得数・変化数・抽出数・トークン消費・エラーを残す（ADR 0007, 0010）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LLMUsage(BaseModel):
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, *, input_tokens: int = 0, output_tokens: int = 0, cached: bool = False) -> None:
        self.calls += 1
        if cached:
            self.cached_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class RunReport(BaseModel):
    service: str
    command: str
    started_at: datetime
    finished_at: datetime | None = None
    stages: dict[str, dict[str, int]] = Field(default_factory=dict)
    llm: LLMUsage = Field(default_factory=LLMUsage)
    errors: list[str] = Field(default_factory=list)
    extraction_metrics: dict | None = None
    notes: list[str] = Field(default_factory=list)

    def bump(self, stage: str, key: str, n: int = 1) -> None:
        counts = self.stages.setdefault(stage, {})
        counts[key] = counts.get(key, 0) + n
