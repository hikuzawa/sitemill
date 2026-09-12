"""判定結果を人が読む形にする（ADR 0019）。

申請する順の表・保留の表・除外の表（理由つき）の 3 つ。表だけ見て申請できるように、
根拠は略さずセルに入れる。
"""

from __future__ import annotations

from sitemill.affiliate.models import Screened
from sitemill.affiliate.screen import UNKNOWN_TIER, ScreenResult

_COLUMNS = (
    "#",
    "案件名",
    "ASP",
    "広告主",
    "種別",
    "報酬",
    "成果条件",
    "確定率",
    "EPC",
    "地域",
    "審査",
    "点",
    "根拠",
)


def markdown_report(result: ScreenResult) -> str:
    """申請順の表・保留・除外理由をまとめた Markdown を返す。"""
    out: list[str] = [
        f"# 案件の選定結果（プロファイル: {result.profile_name}）",
        "",
        f"申請 {len(result.applying)} 件 / 保留 {len(result.holding)} 件 / "
        f"除外 {len(result.rejected)} 件（合計 {len(result.items)} 件）",
        "",
        "## 申請する順",
        "",
    ]
    out += _table(result.applying) if result.applying else ["採用できる案件が無い。", ""]

    if result.holding:
        out += ["## 保留（今は出さないが、候補として残す）", ""]
        out += _table(result.holding)

    out += ["## 除外", ""]
    if result.rejected:
        out += ["| # | 案件名 | 広告主 | 除外理由 |", "| --- | --- | --- | --- |"]
        for i, s in enumerate(result.rejected, 1):
            out.append(
                f"| {i} | {_cell(s.candidate.name)} | {_cell(s.candidate.advertiser)} "
                f"| {_cell('。'.join(s.reasons))} |"
            )
        out.append("")
    else:
        out += ["除外した案件は無い。", ""]

    notes = [s for s in result.items if s.candidate.notes]
    if notes:
        out += ["## 読めなかった項目（貼り付け元を確かめる）", ""]
        for s in notes:
            out.append(f"- {s.candidate.name}: {'、'.join(s.candidate.notes)}")
        out.append("")
    return "\n".join(out)


def _table(rows: list[Screened]) -> list[str]:
    out = [
        "| " + " | ".join(_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in _COLUMNS) + " |",
    ]
    for i, s in enumerate(rows, 1):
        c = s.candidate
        out.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    _cell(c.name),
                    _cell(c.asp),
                    _cell(c.advertiser),
                    _cell(s.kind),
                    _cell(c.reward_label),
                    _cell(_condition_cell(s)),
                    c.approval_label,
                    c.epc_label,
                    _cell(" / ".join(c.region_quotes) if c.region_quotes else "制限なし"),
                    _cell(c.review_label),
                    f"{s.score:.0f}",
                    _cell("。".join(s.reasons)),
                ]
            )
            + " |"
        )
    out.append("")
    return out


def _condition_cell(s: Screened) -> str:
    """段が決まっていればその名前、決まらなければ成果条件の原文を出す。

    段に当てはまらなくても原文は読めていることがある（A8 は成果報酬の欄に条件が同居する）。
    そこで「不明」と出すと、読めた原文が表から消える。
    """
    if s.condition_tier and s.condition_tier != UNKNOWN_TIER:
        return s.condition_tier
    return s.candidate.condition or UNKNOWN_TIER


def _cell(text: str) -> str:
    """表のセルに入れられる形にする。改行と縦棒を潰す。"""
    return (text or "").replace("|", "｜").replace("\n", " ").strip() or "-"
