"""案件選定の構造化・判定・受け渡し（ADR 0019）。

fixture は実在の ASP 画面の複製ではなく、判定の分かれ目を 1 件ずつ持たせて手で書いたもの。
ネットワークは使わない。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sitemill.affiliate import (
    Verdict,
    load_profile,
    markdown_report,
    parse_offers,
    render_code,
    render_handoff,
    screen,
    suggest_offer_id,
)
from sitemill.affiliate.parse import region_quotes

PASTE = Path(__file__).parent / "fixtures" / "affiliate" / "asp-search-owner.txt"
PROFILE = Path(__file__).parent.parent / "profiles" / "akiya-owner.example.yaml"


@pytest.fixture
def profile():
    return load_profile(PROFILE)


@pytest.fixture
def candidates():
    return parse_offers(PASTE.read_text(encoding="utf-8"), asp="a8")


@pytest.fixture
def result(candidates, profile):
    return screen(candidates, profile)


def test_splits_records_without_blank_lines(candidates):
    """空行が無い貼り付けでも、ラベルの無い見出し行で案件が切れる。"""
    assert len(candidates) == 6
    assert candidates[0].name.endswith("【解体工事110番】")
    assert candidates[1].name == "不動産一括査定サイト【おうち査定】"
    assert candidates[5].name == "天然水ウォーターサーバー無料お試し"


def test_values_come_from_quotes(candidates):
    """数値は原文の引用から決定的パーサが作る。"""
    c = candidates[0]
    assert c.asp == "a8"
    assert c.advertiser == "シェアリングテクノロジー株式会社"
    assert c.reward_quote == "8,000円"
    assert c.reward_yen == 8000
    assert c.approval_rate == 82.5
    assert c.epc_yen == 96
    assert c.cookie_days == 90
    assert c.review_required is False
    assert c.condition.startswith("WEBまたは電話申込後")
    assert candidates[1].review_required is True


def test_missing_fields_stay_unknown(candidates):
    """記載の無い項目は埋めずに注記を残す。"""
    water = candidates[5]
    assert water.epc_yen is None
    assert water.cookie_days is None
    assert "EPC の記載なし" in water.notes


def test_ranking_and_verdicts(result):
    """申請するものが先、除外は理由つきで後ろ。"""
    applying = [s.candidate.name for s in result.applying]
    assert applying[0].startswith("不動産一括査定サイト")
    assert any("解体工事110番" in n for n in applying)
    assert len(applying) == 2
    assert [s.verdict for s in result.items[:2]] == [Verdict.apply, Verdict.apply]

    rejected = {s.candidate.name: "。".join(s.reasons) for s in result.rejected}
    assert len(rejected) == 4
    assert "首都圏" in next(v for k, v in rejected.items() if "かたづけ本舗" in k)
    assert "投資" in next(v for k, v in rejected.items() if "投資セミナー" in k)
    assert "確定率" in next(v for k, v in rejected.items() if "ぬりかえ広場" in k)
    assert "所有者向け" in next(v for k, v in rejected.items() if "ウォーターサーバー" in k)


def test_kind_and_placements_come_from_profile(result):
    kaitai = next(s for s in result.items if "解体工事110番" in s.candidate.name)
    assert kaitai.kind == "解体"
    assert kaitai.placements == ("owners-consult", "owners-flow-demolition")
    assert kaitai.condition_tier == "申込後に事業者の手配まで"


def test_score_breakdown_sums_to_score(result):
    for s in result.applying + result.holding:
        assert s.score == pytest.approx(sum(s.breakdown.values()))
        assert 0 <= s.score <= 100


def test_region_quotes_ignore_advertiser_address():
    """広告主の所在地は地域制限として拾わない。"""
    assert region_quotes("株式会社サンプル\n所在地 東京都渋谷区1-1-1") == ()
    assert region_quotes("対応エリア 東京都のみ")


def test_low_score_is_held_not_rejected(profile, candidates):
    """落とす理由は無いが点が届かないものは保留にする（除外表には出さない）。"""
    strict = replace(profile, thresholds=replace(profile.thresholds, min_score=95.0))
    held = screen(candidates, strict)
    assert len(held.applying) == 0
    assert len(held.holding) == 2
    assert all("届かない" in "".join(s.reasons) for s in held.holding)


def test_markdown_report_has_table_and_reasons(result):
    md = markdown_report(result)
    assert "## 申請する順" in md
    assert "## 除外" in md
    assert "| 確定率 |" in md
    assert "首都圏" in md
    assert "\n\n\n" not in md


def test_emit_handoff_matches_the_handover_form(result, profile):
    kaitai = next(s for s in result.items if "解体工事110番" in s.candidate.name)
    data = yaml.safe_load(
        render_handoff(kaitai, profile, offer_id="kaitai-110", today="2026-09-12")
    )
    assert data["offer_id"] == "kaitai-110"
    assert data["kind"] == "解体"
    assert data["asp"] == "a8"
    assert data["cookie_days"] == 90
    assert data["placements"] == ["owners-consult", "owners-flow-demolition"]
    assert data["tracking_url"] == ""  # 契約前は空のまま（ダミーを入れない）
    assert data["approved_on"] == "2026-09-12"
    assert data["reward_condition"].startswith("WEBまたは電話申込後")


def test_emit_code_is_valid_python(result, profile):
    kaitai = next(s for s in result.items if "解体工事110番" in s.candidate.name)
    code = render_code(kaitai, profile, offer_id="kaitai-110", today="2026-09-12")
    assert 'id="kaitai-110"' in code
    assert 'placements=("owners-consult", "owners-flow-demolition",)' in code
    assert "cookie_days=90" in code
    compile(f"Offer = dict\nx = {code}", "<emit>", "exec")


def test_emit_code_leaves_none_for_missing_numbers(result, profile):
    water = next(s for s in result.items if "ウォーターサーバー" in s.candidate.name)
    code = render_code(water, profile, offer_id="x", today="2026-09-12")
    assert "cookie_days=None" in code


def test_suggest_offer_id_does_not_invent_romaji():
    assert suggest_offer_id("【解体工事110番】") == "110"
    assert suggest_offer_id("遺品整理") == ""
