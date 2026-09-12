"""案件の判定と並べ替え（ADR 0019）。

判定は 2 段。まず「出してはいけないもの」を落とし（除外）、残りに点をつけて申請順に並べる。
落とす理由は無いが今は申請しない、というものは除外せず保留にする。

除外（1 つでも当たれば不採用。順に見て、最初に当たった理由をそのまま出す）
1. 信頼性: プロファイルの `exclusions` に当たる（情報商材・投資セミナーなど）
2. 導線: どの導線にも当てはまらない
3. しきい値: 確定率・報酬が下限を割る（EPC の下限は除外ではなく保留。ADR 0022）

点（満点 100。内訳はプロファイルの `weights`）
- 導線適合: 案件名に導線の語があれば満点、成果条件や広告主名だけなら減点
- 成果条件の重さ: 軽い（無料見積・資料請求）ほど高い。読めなければ既定の比で置く
- 確定率: 下限から 100% までを線形に割り当てる。記載が無ければ既定の比で置く
- 稼ぎ: EPC があれば EPC、無ければ報酬額

保留は 4 通り。どれも除外せず、点と根拠をつけたまま表に残す。理由は根拠の先頭に出す。
- 成果の出る地域がサイトの対象地域と重ならない（ADR 0021）。
  今の枠には出せないが、将来その地域のページにだけ出す候補として残す
- 確定率が読めない（ADR 0022）。低いのではなく分からないので、申請の判断材料が足りない
- EPC が下限未満（ADR 0022）。**確定率と EPC の両方が読めているときだけ**見る。
  確定率が高くても、実際には申し込まれていない案件を弾く
- 点が `thresholds.min_score` に届かない（落とす理由はないが、今すぐ申請する理由もない）
"""

from __future__ import annotations

from dataclasses import dataclass

from sitemill.affiliate.models import Candidate, Screened, Verdict, number
from sitemill.affiliate.profile import Funnel, Profile
from sitemill.parse.jp import normalize_text

# 成果条件の段を決められなかったときの段の名前。表と点の両方で使う
UNKNOWN_TIER = "不明"


@dataclass(frozen=True)
class ScreenResult:
    profile_name: str
    items: tuple[Screened, ...]  # 申請順。除外・保留は後ろ

    @property
    def applying(self) -> list[Screened]:
        return [s for s in self.items if s.verdict is Verdict.apply]

    @property
    def holding(self) -> list[Screened]:
        return [s for s in self.items if s.verdict is Verdict.hold]

    @property
    def rejected(self) -> list[Screened]:
        return [s for s in self.items if s.verdict is Verdict.reject]

    def to_dict(self) -> dict[str, object]:
        return {"profile": self.profile_name, "items": [s.to_dict() for s in self.items]}


def screen(candidates: list[Candidate], profile: Profile) -> ScreenResult:
    """案件を判定し、申請すべき順に並べる。"""
    scored = [_screen_one(c, profile) for c in candidates]
    scored.sort(key=_order)
    return ScreenResult(profile_name=profile.name, items=tuple(scored))


def _order(s: Screened) -> tuple[int, float, int, str]:
    """申請順: 申請 → 保留 → 除外。同じ点なら審査のあるものを先に出す（承認に日数がかかる）。"""
    rank = {Verdict.apply: 0, Verdict.hold: 1, Verdict.reject: 2}[s.verdict]
    review_first = 0 if s.candidate.review_required else 1
    return (rank, -s.score, review_first, s.candidate.name)


def _screen_one(cand: Candidate, profile: Profile) -> Screened:
    haystack = _haystack(cand)
    reasons: list[str] = []

    # 1. 信頼性
    for ex in profile.exclusions:
        hit = _first_hit(haystack, ex.keywords)
        if hit:
            return _reject(cand, f"{ex.reason}（「{hit}」）")

    # 2. 導線
    funnel, funnel_score, funnel_why = _match_funnel(cand, profile)
    if funnel is None:
        kinds = "・".join(f.kind for f in profile.funnels)
        return _reject(cand, f"サイトの導線（{kinds}）に当てはまらない")
    reasons.append(funnel_why)

    # 地域が合わないものは落とさず、点をつけたうえで保留に回す（ADR 0021）
    region = _region_problem(cand, profile)

    # 3. しきい値
    th = profile.thresholds
    if th.min_approval_rate is not None and cand.approval_rate is not None:
        if cand.approval_rate < th.min_approval_rate:
            return _reject(
                cand,
                f"確定率 {cand.approval_rate:g}% が下限 {th.min_approval_rate:g}% 未満",
                kind=funnel.kind,
            )
    if (
        th.min_reward_yen is not None
        and cand.reward_yen is not None
        and cand.reward_yen < th.min_reward_yen
    ):
        return _reject(
            cand,
            f"報酬 {cand.reward_yen:,}円 が下限 {th.min_reward_yen:,}円 未満",
            kind=funnel.kind,
        )

    # 除外はしないが、このままでは申請しない理由
    holds = _hold_reasons(cand, profile, region)

    # 点をつける
    w = profile.weights
    tier_id, tier_score, tier_why = _condition_score(cand, profile)
    approval_score, approval_why = _approval_score(cand, profile)
    earning_score, earning_why = _earning_score(cand, profile)
    reasons += [tier_why, approval_why, earning_why]
    if th.warn_approval_rate is not None and cand.approval_rate is not None:
        if cand.approval_rate < th.warn_approval_rate:
            reasons.append(f"確定率が {th.warn_approval_rate:g}% を下回る。成果の取りこぼしに注意")
    if cand.notes:
        reasons.append("読めなかった項目: " + "、".join(cand.notes))

    breakdown = {
        "導線": funnel_score * w.funnel,
        "成果条件": tier_score * w.condition,
        "確定率": approval_score * w.approval_rate,
        "稼ぎ": earning_score * w.earning,
    }
    total = sum(breakdown.values())
    if total < th.min_score:
        holds.append(f"合計 {total:.0f} 点が申請の目安 {th.min_score:g} 点に届かない")
    return Screened(
        candidate=cand,
        verdict=Verdict.hold if holds else Verdict.apply,
        score=total,
        kind=funnel.kind,
        placements=funnel.placements,
        condition_tier=tier_id,
        region_limited=bool(region),
        breakdown=breakdown,
        reasons=tuple([*holds, *reasons]),  # 保留の理由を先に出す
    )


def _hold_reasons(cand: Candidate, profile: Profile, region: str | None) -> list[str]:
    """除外はしないが、このままでは申請しない理由。空なら申請してよい。"""
    th = profile.thresholds
    out: list[str] = [region] if region else []
    if cand.approval_rate is None:
        out.append("確定率が読めない。申請するかどうかの材料が足りないので保留にする")
    elif th.min_epc_yen is not None and cand.epc_yen is not None:
        # 確定率と EPC の両方が読めているときだけ見る。確定率が高くても、
        # EPC が低いなら実際には申し込まれていない
        if cand.epc_yen < th.min_epc_yen:
            out.append(
                f"EPC {cand.epc_label} が下限 {number(th.min_epc_yen)}円 未満"
                f"（確定率 {cand.approval_label} でも申し込まれていない）"
            )
    return out


def _reject(cand: Candidate, reason: str, *, kind: str = "") -> Screened:
    return Screened(candidate=cand, verdict=Verdict.reject, score=0.0, kind=kind, reasons=(reason,))


def _haystack(cand: Candidate) -> str:
    return normalize_text(" ".join([cand.name, cand.advertiser, cand.condition, cand.raw])).lower()


def _first_hit(haystack: str, keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        if normalize_text(kw).lower() in haystack:
            return kw
    return None


def _match_funnel(cand: Candidate, profile: Profile) -> tuple[Funnel | None, float, str]:
    """どの導線に合うか。案件名に語があるものを優先し、次に成果条件・広告主名を見る。"""
    best: tuple[float, Funnel, str] | None = None
    name = normalize_text(cand.name).lower()
    near = normalize_text(f"{cand.condition} {cand.advertiser}").lower()
    far = normalize_text(cand.raw).lower()
    for funnel in profile.funnels:
        for where, weight, label in (
            (name, 1.0, "案件名"),
            (near, 0.7, "成果条件・広告主名"),
            (far, 0.5, "掲載文"),
        ):
            hit = _first_hit(where, funnel.keywords)
            if hit:
                why = f"{funnel.kind}の導線に合う（{label}の「{hit}」）"
                if best is None or weight > best[0]:
                    best = (weight, funnel, why)
                break
    if best is None:
        return None, 0.0, ""
    weight, funnel, why = best
    return funnel, weight, why


def _region_problem(cand: Candidate, profile: Profile) -> str | None:
    if not cand.region_quotes:
        return None
    joined = " / ".join(cand.region_quotes)
    if "全国" in joined:
        return None
    if not profile.service_area:
        return None
    for area in profile.service_area:
        if area in joined or area.rstrip("都道府県") in joined:
            return None
    return f"{profile.region_reason}（{joined}）"


def _condition_score(cand: Candidate, profile: Profile) -> tuple[str, float, str]:
    text = normalize_text(cand.condition or cand.raw).lower()
    for tier in profile.conditions:
        hit = _first_hit(text, tier.keywords)
        if hit:
            return tier.id, tier.score, f"成果条件は「{tier.id}」（「{hit}」）"
    fallback = profile.thresholds.unknown_condition_score
    return UNKNOWN_TIER, fallback, "成果条件の重さを判定できなかった（既定の配点で置いた）"


def _approval_score(cand: Candidate, profile: Profile) -> tuple[float, str]:
    if cand.approval_rate is None:
        return profile.thresholds.unknown_approval_score, "確定率の記載なし（既定の配点で置いた）"
    floor = profile.thresholds.min_approval_rate or 0.0
    span = max(100.0 - floor, 1.0)
    score = min(1.0, max(0.0, (cand.approval_rate - floor) / span))
    return score, f"確定率 {cand.approval_rate:g}%"


def _earning_score(cand: Candidate, profile: Profile) -> tuple[float, str]:
    e = profile.earning
    if cand.epc_yen is not None:
        return min(1.0, cand.epc_yen / max(e.epc_full_yen, 1)), f"EPC {cand.epc_label}"
    if cand.reward_yen is not None:
        return (
            min(1.0, cand.reward_yen / max(e.reward_full_yen, 1)),
            f"報酬 {cand.reward_yen:,}円（EPC の記載が無いので報酬額で見た）",
        )
    if cand.reward_rate is not None:
        return 0.5, f"報酬は売上の {cand.reward_rate:g}%（金額に直せないので中間に置いた）"
    return 0.0, "報酬も EPC も読めなかった"
