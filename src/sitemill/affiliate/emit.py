"""承認された案件を、掲載側へ渡す形にする（ADR 0019）。

選定に使ったのと同じ構造化データから、サービスの受け渡し様式（YAML）と登録用のコード片を作る。
手で書き写す工程を挟まないので、表に出ていた値とコードの値がずれない。

様式はサービスごとに違う（掲載側のデータ構造が違う）ので、エンジンは雛形を持たない。
プロファイルの `emit.handoff` と `emit.code` に書いた雛形の `{...}` を埋めるだけにする。

埋められる名前:
  offer_id / name / advertiser / asp / kind / label / condition / reward_label /
  reward_yen / approval_rate / epc_yen / cookie_days / placements / today / score
契約後にしか分からない項目（計測 URL など）は雛形に空のまま書いておく。
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from sitemill.affiliate.models import Screened
from sitemill.affiliate.profile import Profile
from sitemill.clock import jst_today

_LATIN = re.compile(r"[0-9A-Za-z]+")


class EmitError(ValueError):
    """雛形が無い、または埋められない。"""


def suggest_offer_id(name: str) -> str:
    """案件名から `/go/<id>` に使える候補を作る。作れなければ空を返す（推測で当てない）。"""
    tokens = [t.lower() for t in _LATIN.findall(name)]
    return "-".join(tokens)[:40].strip("-")


def values(screened: Screened, *, offer_id: str = "", today: str = "") -> dict[str, Any]:
    """雛形に埋める値。数値は選定時に決定的パーサが作ったものをそのまま使う。"""
    c = screened.candidate
    return {
        "offer_id": offer_id or suggest_offer_id(c.name),
        "name": c.name,
        "advertiser": c.advertiser,
        "asp": c.asp,
        "kind": screened.kind,
        "label": c.name,
        "condition": c.condition,
        "reward_label": c.reward_label,
        "reward_yen": c.reward_yen,
        "approval_rate": c.approval_rate,
        "epc_yen": c.epc_yen,
        "cookie_days": c.cookie_days,
        "placements": list(screened.placements),
        "today": today or jst_today().isoformat(),
        "score": round(screened.score),
    }


def render_handoff(screened: Screened, profile: Profile, **kw: Any) -> str:
    """`emit.handoff` の雛形を埋めて YAML にする。"""
    template = profile.emit.get("handoff")
    if not isinstance(template, dict):
        raise EmitError("プロファイルに emit.handoff が無い。受け渡し様式を書く")
    filled = _fill(template, values(screened, **kw))
    return yaml.safe_dump(filled, allow_unicode=True, sort_keys=False, width=100)


def render_code(screened: Screened, profile: Profile, **kw: Any) -> str:
    """`emit.code` の雛形を埋めてコード片にする。"""
    template = profile.emit.get("code")
    if not isinstance(template, str) or not template.strip():
        raise EmitError("プロファイルに emit.code が無い。登録用のコード雛形を書く")
    return _fill_str(template, values(screened, **kw), none_as="None")


def _fill(node: Any, vals: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _fill(v, vals) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, vals) for v in node]
    if isinstance(node, str):
        whole = _WHOLE.fullmatch(node.strip())
        if whole and whole.group(1) in vals:
            return vals[whole.group(1)]  # 配列・数値・None はそのままの型で入れる
        return _fill_str(node, vals)
    return node


_WHOLE = re.compile(r"\{([a-z_]+)\}")


def _fill_str(text: str, vals: dict[str, Any], *, none_as: str = "") -> str:
    """雛形の `{...}` を埋める。値が無い項目は none_as にする（コードでは None、YAML では空）。"""

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in vals:
            return m.group(0)
        value = vals[key]
        if value is None:
            return none_as
        if isinstance(value, list):
            return ", ".join(f'"{v}"' for v in value)
        return str(value)

    return _WHOLE.sub(sub, text)
