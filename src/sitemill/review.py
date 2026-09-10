"""レビュー待ち行列（ADR 0007）。低確信の候補だけを人間に表で示し、行ごとに決定を受け取る。

高確信の候補は自動採用（policy=crawl / link_only）され、ここには pending だけが残る想定。
表の列: 対象・候補URL・分類・確信度・根拠の引用・提案アクション（承認/却下/URL修正）。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ACTIONS = ("承認", "却下", "URL修正")


class ReviewCandidate(BaseModel):
    key: str  # 対象の一意キー（例: 市町村コード）
    label: str  # 表示名（例: 長野県 東御市）
    url: str | None = None  # 候補ページ URL（見つからなければ None）
    page_class: str | None = None  # 分類結果（listing_index / spa / third_party / ...）
    class_label: str | None = None  # 分類の日本語ラベル
    confidence: float = 0.0
    operator_kind: str = "unknown"
    evidence_quote: str | None = None
    evidence_url: str | None = None
    reason: str = ""  # レビューが要る理由 / 自動採用の理由
    proposed_policy: str = "pending"  # crawl / link_only / pending
    proposed_action: str = "承認"  # 人間への提案（承認/却下/URL修正）
    decision: str | None = None  # 人間の決定（承認/却下/新URL文字列）。未記入は None
    note: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.proposed_policy == "pending"


class ReviewQueue(BaseModel):
    prefecture: str
    prefecture_slug: str
    created_at: str
    candidates: list[ReviewCandidate] = Field(default_factory=list)

    def pending(self) -> list[ReviewCandidate]:
        return [c for c in self.candidates if c.needs_review]

    def auto_adopted(self) -> list[ReviewCandidate]:
        return [c for c in self.candidates if not c.needs_review]

    @classmethod
    def load(cls, path: Path) -> ReviewQueue:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
            encoding="utf-8",
            newline="\n",
        )


def _short_url(url: str | None, width: int = 48) -> str:
    if not url:
        return "（未発見）"
    return url if len(url) <= width else url[: width - 1] + "…"


def _short_quote(text: str | None, width: int = 40) -> str:
    if not text:
        return "—"
    t = " ".join(text.split())
    return t if len(t) <= width else t[: width - 1] + "…"


def render_review_table(candidates: list[ReviewCandidate]) -> str:
    """pending 候補を Markdown 表にする。行ごとに人間が返事する用。"""
    header = (
        "| # | 対象 | 候補URL | 分類 | 確信度 | 根拠 | 提案アクション |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows = []
    for i, c in enumerate(candidates, 1):
        rows.append(
            f"| {i} | {c.label} | {_short_url(c.url)} | {c.class_label or c.page_class or '—'} "
            f"| {c.confidence:.2f} | {_short_quote(c.evidence_quote or c.reason)} "
            f"| {c.proposed_action} |"
        )
    return "\n".join([header, *rows]) if rows else "（レビュー待ちの候補はありません）"
