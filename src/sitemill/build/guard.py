"""公開前の歯止め: 判定の内訳が急に悪くなったら配置を止める（ADR 0026、2026-09-28）。

japan-open-today で、鮮度の測り方の誤りから本番の判定が 2 日間「開いている 8・不明 152」になった。
テストも検査も通り、週次のまとめで初めて気づいた。**「不明が急に増えた」状態を公開するより、前日の
本番を残すほうがまし**なので、ビルドのあと配置の前にこの検査を置き、越えたら配置を止めて知らせる。

サービスは `publish_metrics(ws, *, now) -> dict[str, GuardMetric]`（件数と全体）と
`publish_limits: dict[str, GuardLimit]`（しきい値と、その理由）を持つ。無ければ何もしない。
比べる相手は**前回この検査を通して公開した値**（`data/state/publish_guard.json`）。止めた回の値では
更新しないので、じわじわ増える場合も、止まったあとの翌日も、最後に公開した状態と比べ続ける。
人が確かめて正当な変化と判断したときは `sitemill guard --accept` で今の値を基準にする。

見るのは「割合」で、件数は割合の分母と一緒に記録する。施設数が増えた日に件数だけで比べると、
増えた分がそのまま「悪化」に見える。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sitemill.settings import Workspace

BASELINE_FILE = "publish_guard.json"


@dataclass(frozen=True)
class GuardMetric:
    """件数と全体。割合で比べる。"""

    count: int
    total: int

    @property
    def share(self) -> float:
        return self.count / self.total if self.total else 0.0


@dataclass(frozen=True)
class GuardLimit:
    """しきい値。`reason` は必須（なぜこの値かを、平常時の幅と事故の値で書く）。

    - `max_rise`: 前回公開した割合からの増え幅の上限（0.10 = 10 ポイント）
    - `max_share`: 割合そのものの上限（じわじわ増える場合の歯止め）
    """

    reason: str
    max_rise: float | None = None
    max_share: float | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("GuardLimit には理由（reason）が要る")


@dataclass
class GuardResult:
    values: dict[str, GuardMetric] = field(default_factory=dict)
    baseline: dict[str, dict[str, Any]] = field(default_factory=dict)
    breaches: list[str] = field(default_factory=list)
    accepted: bool = False  # 基準を今の値に置き換えたか

    @property
    def ok(self) -> bool:
        return not self.breaches


def evaluate(
    values: dict[str, GuardMetric],
    baseline: dict[str, dict[str, Any]],
    limits: dict[str, GuardLimit],
) -> list[str]:
    """しきい値を越えたものを文で返す。基準が無い指標は上限だけを見る。"""
    breaches: list[str] = []
    for name, limit in limits.items():
        metric = values.get(name)
        if metric is None:
            breaches.append(f"{name}: サービスが値を返さなかった")
            continue
        now = metric.share
        if limit.max_share is not None and now > limit.max_share:
            breaches.append(
                f"{name}: {now:.1%}（{metric.count}/{metric.total}）が"
                f"上限 {limit.max_share:.0%} を超えた — {limit.reason}"
            )
        before = baseline.get(name)
        if limit.max_rise is not None and before is not None:
            rise = now - float(before.get("share", 0.0))
            if rise > limit.max_rise:
                breaches.append(
                    f"{name}: 前回公開した {float(before['share']):.1%}"
                    f"（{before.get('count')}/{before.get('total')}）から {now:.1%}"
                    f"（{metric.count}/{metric.total}）へ +{rise * 100:.1f}pt。"
                    f"上限は +{limit.max_rise * 100:.0f}pt — {limit.reason}"
                )
    return breaches


def load_baseline(ws: Workspace) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((ws.state_dir / BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data.get("metrics") or {})


def save_baseline(
    ws: Workspace, values: dict[str, GuardMetric], *, now: datetime, how: str
) -> None:
    path = ws.state_dir / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "accepted_at": now.isoformat(),
        "how": how,  # "passed"（検査を通った）/ "manual"（人が --accept で受け入れた）
        "metrics": {
            name: {"count": m.count, "total": m.total, "share": round(m.share, 6)}
            for name, m in sorted(values.items())
        },
    }
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def run_guard(
    ws: Workspace, service: object, *, now: datetime, accept: bool = False
) -> GuardResult:
    """サービスの指標を基準と比べる。通れば（または accept なら）基準を今の値にする。"""
    hook = getattr(service, "publish_metrics", None)
    limits: dict[str, GuardLimit] = dict(getattr(service, "publish_limits", {}) or {})
    if hook is None or not limits:
        return GuardResult()
    values: dict[str, GuardMetric] = dict(hook(ws, now=now))
    baseline = load_baseline(ws)
    result = GuardResult(values=values, baseline=baseline)
    result.breaches = evaluate(values, baseline, limits)
    if result.ok or accept:
        save_baseline(ws, values, now=now, how="manual" if (accept and not result.ok) else "passed")
        result.accepted = True
    return result
