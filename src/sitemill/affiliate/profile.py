"""判定プロファイル（ADR 0019）。

「どの導線に合うか」「どの地域で成果が出るか」「何を出さないか」はサービス固有なので、
エンジンには持たせず YAML で外から渡す。雛形は sitemill の `profiles/` にある。

重みとしきい値もここに書く。エンジンの中に数字を隠さないので、判定が気に入らなければ
コードを直さずプロファイルを直せる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ProfileError(ValueError):
    """プロファイルの書き方が誤っている。"""


@dataclass(frozen=True)
class Funnel:
    """サイトの導線 1 本。`kind` は掲載側の種別（akiya-atlas なら affiliates.py の KIND_*）。"""

    kind: str
    keywords: tuple[str, ...]
    label: str = ""
    placements: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConditionTier:
    """成果条件の重さの段。上に書いたものから順に当てる（先に当たった段を採る）。"""

    id: str
    score: float  # 0.0（重い）〜 1.0（軽い）
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Exclusion:
    """1 つでも当たれば除外する条件。`reason` はそのまま除外理由として出力に出る。"""

    id: str
    reason: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Thresholds:
    min_approval_rate: float | None = None  # 確定率がこれ未満なら除外
    # EPC がこれ未満なら保留（確定率と EPC の両方が読めているときだけ見る。ADR 0022）
    min_epc_yen: float | None = None
    min_reward_yen: int | None = None
    min_score: float = 50.0  # これ未満は「保留」（除外ではない）
    warn_approval_rate: float | None = None  # これ未満なら注記を付ける
    unknown_approval_score: float = 0.4  # 確定率が不明なときの配点比
    unknown_condition_score: float = 0.5  # 成果条件が読めなかったときの配点比


@dataclass(frozen=True)
class Weights:
    """満点 100 の内訳。合計が 100 でなくても比率として働くが、揃えておくと読みやすい。"""

    funnel: float = 40.0
    condition: float = 25.0
    approval_rate: float = 20.0
    earning: float = 15.0

    @property
    def total(self) -> float:
        return self.funnel + self.condition + self.approval_rate + self.earning


@dataclass(frozen=True)
class Earning:
    """稼ぎの配点。EPC があれば EPC を、無ければ報酬額を使う。"""

    epc_full_yen: int = 100  # これ以上で満点
    reward_full_yen: int = 10_000  # これ以上で満点


# 地域が合わない案件を保留にするときの既定の理由（ADR 0021）。サービスごとの言い回しは
# プロファイルの region.reason で上書きする
DEFAULT_REGION_REASON = "サイトの対象地域と重ならないので今の枠には出せない"


@dataclass(frozen=True)
class Profile:
    name: str
    funnels: tuple[Funnel, ...]
    conditions: tuple[ConditionTier, ...] = ()
    exclusions: tuple[Exclusion, ...] = ()
    service_area: tuple[str, ...] = ()  # 成果が出る地域。空なら全国
    region_reason: str = DEFAULT_REGION_REASON
    thresholds: Thresholds = field(default_factory=Thresholds)
    weights: Weights = field(default_factory=Weights)
    earning: Earning = field(default_factory=Earning)
    site: str = ""
    # 承認後の受け渡し様式。サービスごとに違うのでエンジンは雛形を持たない（emit.py）
    emit: dict[str, Any] = field(default_factory=dict)

    def funnel_for(self, kind: str) -> Funnel | None:
        return next((f for f in self.funnels if f.kind == kind), None)


def _tuple(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ProfileError(f"{where} は文字列の配列で書く: {value!r}")


def load_profile(path: Path) -> Profile:
    """YAML を読む。既定値に頼れるよう、書いていない項目は省略できる。"""
    if not path.is_file():
        raise ProfileError(f"プロファイルが無い: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"プロファイルの中身が辞書でない: {path}")
    return from_dict(data, name=data.get("name") or path.stem)


def from_dict(data: dict[str, Any], *, name: str = "") -> Profile:
    funnels = tuple(
        Funnel(
            kind=str(f["kind"]),
            keywords=_tuple(f.get("keywords"), where=f"funnels[{f.get('kind')}].keywords"),
            label=str(f.get("label", "")),
            placements=_tuple(f.get("placements"), where="funnels.placements"),
        )
        for f in data.get("funnels", [])
        if isinstance(f, dict) and f.get("kind")
    )
    if not funnels:
        raise ProfileError("funnels が 1 つも無い。どの導線に合えば採るのかを書く")

    conditions = tuple(
        ConditionTier(
            id=str(c["id"]),
            score=float(c.get("score", 0.5)),
            keywords=_tuple(c.get("keywords"), where="conditions.keywords"),
        )
        for c in data.get("conditions", [])
        if isinstance(c, dict) and c.get("id")
    )
    exclusions = tuple(
        Exclusion(
            id=str(e["id"]),
            reason=str(e.get("reason", e["id"])),
            keywords=_tuple(e.get("keywords"), where="exclusions.keywords"),
        )
        for e in data.get("exclusions", [])
        if isinstance(e, dict) and e.get("id")
    )

    th = data.get("thresholds") or {}
    we = data.get("weights") or {}
    ea = data.get("earning") or {}
    region = data.get("region") or {}
    return Profile(
        name=str(data.get("name") or name),
        site=str(data.get("site", "")),
        funnels=funnels,
        conditions=conditions,
        exclusions=exclusions,
        service_area=_tuple(region.get("service_area"), where="region.service_area"),
        region_reason=str(region.get("reason", DEFAULT_REGION_REASON)),
        thresholds=Thresholds(
            min_approval_rate=_opt_float(th.get("min_approval_rate")),
            min_epc_yen=_opt_float(th.get("min_epc_yen")),
            min_reward_yen=_opt_int(th.get("min_reward_yen")),
            min_score=float(th.get("min_score", 50.0)),
            warn_approval_rate=_opt_float(th.get("warn_approval_rate")),
            unknown_approval_score=float(th.get("unknown_approval_score", 0.4)),
            unknown_condition_score=float(th.get("unknown_condition_score", 0.5)),
        ),
        weights=Weights(
            funnel=float(we.get("funnel", 40.0)),
            condition=float(we.get("condition", 25.0)),
            approval_rate=float(we.get("approval_rate", 20.0)),
            earning=float(we.get("earning", 15.0)),
        ),
        emit=dict(data.get("emit") or {}),
        earning=Earning(
            epc_full_yen=int(ea.get("epc_full_yen", 100)),
            reward_full_yen=int(ea.get("reward_full_yen", 10_000)),
        ),
    )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)
