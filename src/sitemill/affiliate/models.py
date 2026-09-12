"""案件選定のデータ（ADR 0019）。

ASP の検索結果から取り出した 1 案件（`Candidate`）と、判定の結果（`Screened`）。

数値は必ず原文の引用から決定的パーサで作る（quote-then-parse、ADR 0004）。
引用が無い項目は `None` のままにして「不明」と出す。推定で埋めない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    """案件をどうするか。"""

    apply = "申請"
    hold = "保留"
    reject = "除外"


@dataclass(frozen=True)
class Candidate:
    """ASP の検索結果 1 件分。`*_quote` は原文そのまま、値はパーサが作ったもの。"""

    name: str
    asp: str = ""
    advertiser: str = ""
    reward_quote: str = ""
    reward_yen: int | None = None  # 固定額の成果報酬（円）
    reward_rate: float | None = None  # 売上に対する％の案件
    condition: str = ""  # 成果条件の原文
    approval_rate: float | None = None  # 確定率（％）
    epc_yen: int | None = None
    cookie_days: int | None = None  # 再訪問期間
    review_required: bool | None = None  # 提携審査。即時提携なら False
    region_quotes: tuple[str, ...] = ()  # 地域制限として読めた原文
    raw: str = ""  # 貼り付けた原文のブロック
    notes: tuple[str, ...] = ()  # 取れなかった項目

    @property
    def reward_label(self) -> str:
        """表に出す報酬の表記。値にできなかったときは原文を出す。"""
        if self.reward_yen is not None:
            return f"{self.reward_yen:,}円"
        if self.reward_rate is not None:
            return f"売上の{self.reward_rate:g}%"
        return self.reward_quote or "不明"

    @property
    def review_label(self) -> str:
        if self.review_required is None:
            return "不明"
        return "審査あり" if self.review_required else "即時提携"

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        d = asdict(self)
        d["region_quotes"] = list(self.region_quotes)
        d["notes"] = list(self.notes)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in data.items() if k in known}
        kw["region_quotes"] = tuple(kw.get("region_quotes") or ())
        kw["notes"] = tuple(kw.get("notes") or ())
        return cls(**kw)


@dataclass(frozen=True)
class Screened:
    """1 案件の判定。`reasons` は人が読む根拠で、除外ならそのまま除外理由になる。"""

    candidate: Candidate
    verdict: Verdict
    score: float
    kind: str = ""  # 当てはまった導線（掲載側の種別）
    placements: tuple[str, ...] = ()
    condition_tier: str = ""  # 成果条件の重さの段
    # 地域が合わないので保留にしたもの。対応エリアの原文は candidate.region_quotes に残る。
    # 将来その地域のページにだけ出すときの絞り込みに使う（ADR 0021）
    region_limited: bool = False
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    @property
    def rejected(self) -> bool:
        return self.verdict is Verdict.reject

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "verdict": self.verdict.value,
            "score": round(self.score, 1),
            "kind": self.kind,
            "placements": list(self.placements),
            "condition_tier": self.condition_tier,
            "region_limited": self.region_limited,
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Screened:
        return cls(
            candidate=Candidate.from_dict(data["candidate"]),
            verdict=Verdict(data["verdict"]),
            score=float(data.get("score", 0.0)),
            kind=data.get("kind", ""),
            placements=tuple(data.get("placements") or ()),
            condition_tier=data.get("condition_tier", ""),
            region_limited=bool(data.get("region_limited", False)),
            breakdown=dict(data.get("breakdown") or {}),
            reasons=tuple(data.get("reasons") or ()),
        )
