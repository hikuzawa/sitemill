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
from sitemill.affiliate.models import Screened
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
    assert len(rejected) == 3
    assert not any("かたづけ本舗" in k for k in rejected)  # 地域限定は保留に回る（ADR 0021）
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
    assert len(held.holding) == 3  # 点が届かない 2 件と、地域限定の 1 件
    assert all("届かない" in "".join(s.reasons) for s in held.holding)


def test_region_limited_is_held_as_a_candidate(result):
    """地域が合わない案件は除外せず保留にする（ADR 0021）。

    将来その地域の市町村ページにだけ出す可能性があるので、点も対応エリアの原文も残す。
    """
    s = next(x for x in result.items if "かたづけ本舗" in x.candidate.name)
    assert s.verdict is Verdict.hold
    assert s.region_limited is True
    assert s in result.holding and s not in result.rejected
    assert s.candidate.region_quotes  # 対応エリアの原文が残っている
    assert s.kind == "遺品整理" and s.score > 0  # 導線と点もつけたまま
    assert "首都圏" in "。".join(s.reasons)


def test_region_limited_survives_the_json_round_trip(result):
    """保留の印と対応エリアが JSON をまたいで残る（後で地域別に絞り込むため）。"""
    s = next(x for x in result.items if "かたづけ本舗" in x.candidate.name)
    back = Screened.from_dict(s.to_dict())
    assert back.region_limited is True
    assert back.candidate.region_quotes == s.candidate.region_quotes


def test_region_limited_still_loses_to_a_threshold(profile, candidates):
    """地域限定でも、しきい値を割るものは今までどおり除外する。"""
    strict = replace(profile, thresholds=replace(profile.thresholds, min_approval_rate=80.0))
    out = screen(candidates, strict)
    s = next(x for x in out.items if "かたづけ本舗" in x.candidate.name)
    assert s.verdict is Verdict.reject and "確定率" in "。".join(s.reasons)


def test_markdown_report_has_table_and_reasons(result):
    md = markdown_report(result)
    assert "## 申請する順" in md
    assert "## 保留（今は出さないが、候補として残す）" in md
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


# --- 縦並び・空行入りの一覧（A8 の実データで確かめた形。ADR 0022）-----------------

VERTICAL = Path(__file__).parent / "fixtures" / "affiliate" / "asp-list-vertical.txt"
# 案件名として拾ってはいけない画面の部品
CHROME = (
    "サンプルASP ロゴ",
    "一括提携する",
    "未提携",
    "提携申請中",
    "広告主サイト",
    "広告サンプル",
    "セルフバックを見る",
    "プログラム詳細を見る",
    "アイコンについて",
)


@pytest.fixture
def vertical():
    return parse_offers(VERTICAL.read_text(encoding="utf-8"), asp="a8")


@pytest.fixture
def vertical_result(vertical, profile):
    return screen(vertical, profile)


def test_splits_on_repeated_fields_not_on_blank_lines(vertical):
    """案件の内側に空行が 3 行入っても割れない。同じ項目の二度目で切る。"""
    assert len(vertical) == 4
    assert [c.name for c in vertical] == [
        "空き家の解体費用を一括見積【サンプル解体ナビ】",
        "相続した空き家の査定なら【サンプル査定ドットコム】",
        "空き家の買取【サンプル買取センター】",
        "遺品整理・生前整理の【サンプル片づけ本舗】",
    ]
    assert [c.advertiser for c in vertical] == [
        "サンプル解体株式会社",
        "株式会社サンプル査定",
        "サンプル買取株式会社",
        "サンプル片づけ株式会社",
    ]


def test_reads_labels_stacked_above_their_values(vertical):
    """ラベルが単独行・値が次の行でも読む。EPC は小数のまま持つ。"""
    c = vertical[0]
    assert c.reward_yen == 8000
    assert c.approval_rate == 82.5
    assert c.epc_yen == 56.3
    assert c.epc_label == "56.3円"
    assert vertical[3].epc_yen == 120.5
    assert c.review_required is True  # ラベルの無い「未提携」も提携状況として読む


def test_dash_means_no_record_not_a_broken_paste(vertical):
    """「-」は記載なし。読めなかった項目として残し、他の項目の読み取りは壊さない。"""
    c = vertical[1]
    assert c.approval_rate is None
    assert c.epc_yen is None
    assert c.notes == ("確定率の記載なし", "EPC の記載なし")
    assert c.reward_yen == 17000  # 同じ案件の他の項目は読めている


def test_screen_parts_never_become_the_offer(vertical):
    """「広告サンプル」「プログラム詳細を見る」「未提携」などは案件名にも広告主にもしない。"""
    for c in vertical:
        assert c.name not in CHROME
        assert c.advertiser not in CHROME
    assert all("プログラム検索条件" not in c.raw for c in vertical)  # 一覧の見出しも入れない


def test_tiered_reward_keeps_the_quote_and_the_first_amount(vertical):
    """段組みの報酬（▽一般 / ▽ポイントサイト）でも金額を 1 つ決める。"""
    c = vertical[2]
    assert c.reward_yen == 12000
    assert "▽一般" in c.reward_quote


def test_condition_comes_from_the_reward_cell(vertical):
    """成果条件と金額が同じ欄に入る ASP では、金額を外した残りを条件の原文にする。"""
    assert vertical[0].condition == "無料見積依頼"
    assert vertical[1].condition == "新規査定申込"


def test_explicit_separator_splits_records():
    """空行で切れないので、切りたいところには --- を入れる。"""
    text = "\n".join(["案件A", "成果報酬 1,000円", "---", "案件B", "成果報酬 2,000円"])
    got = parse_offers(text)
    assert [c.name for c in got] == ["案件A", "案件B"]


def test_unknown_approval_rate_is_held(vertical_result):
    """確定率が読めない案件は申請に回さない（除外もしない）。"""
    s = next(x for x in vertical_result.items if "サンプル査定ドットコム" in x.candidate.name)
    assert s.verdict is Verdict.hold
    assert "確定率が読めない" in s.reasons[0]


def test_low_epc_is_held_only_when_the_rate_is_also_known(vertical, profile):
    """EPC の下限は、確定率と EPC の両方が読めているときだけ見る。"""
    low = next(c for c in vertical if "サンプル買取センター" in c.name)
    assert low.approval_rate == 100.0 and low.epc_yen == 3.04
    held = screen([low], profile).items[0]
    assert held.verdict is Verdict.hold
    assert "EPC" in held.reasons[0] and "下限" in held.reasons[0]

    # 確定率が読めないなら、保留の理由は EPC ではなく確定率のほう
    blind = screen([replace(low, approval_rate=None)], profile).items[0]
    assert blind.verdict is Verdict.hold
    assert [r for r in blind.reasons if "EPC" in r and "下限" in r] == []
    assert "確定率が読めない" in blind.reasons[0]


def test_report_shows_the_condition_quote_when_no_tier_matched(vertical_result):
    """段に当てはまらなくても、読めた成果条件の原文を表から消さない。"""
    md = markdown_report(vertical_result)
    assert "| 新規査定申込 |" in md  # 段は決まらないが原文は読めている
    assert "| 無料の見積・査定・資料請求 |" in md  # 段が決まればそちらを出す


def test_epc_above_the_floor_still_applies(vertical_result):
    """EPC が下限を超えていれば、保留の理由にはしない。"""
    s = next(x for x in vertical_result.items if "サンプル解体ナビ" in x.candidate.name)
    assert s.verdict is Verdict.apply
    assert s.candidate.epc_yen == 56.3
