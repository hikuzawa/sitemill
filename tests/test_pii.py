"""PII 検出（氏名・電話・メール）とホワイトリストのテスト。"""

from __future__ import annotations

from sitemill.build.pii import (
    allow_also,
    default_jp_gov_policy,
    is_gov_email,
    normalize_phone,
    scan_text,
)


def _kinds(text, **kw):
    return sorted({f.kind for f in scan_text(text, **kw)})


def test_email_flagged_unless_gov() -> None:
    policy = default_jp_gov_policy()
    assert _kinds("連絡は taro@gmail.com へ", policy=policy) == ["email"]
    # 自治体・官公庁ドメインは許可
    assert scan_text("info@city.tomi.nagano.jp", policy=policy) == []
    assert scan_text("madoguchi@example.lg.jp", policy=policy) == []
    assert scan_text("desk@soumu.go.jp", policy=policy) == []


def test_is_gov_email() -> None:
    assert is_gov_email("a@www.city.okaya.lg.jp")
    assert is_gov_email("a@town.manno.lg.jp")
    assert is_gov_email("a@city.zentsuji.kagawa.jp")
    assert not is_gov_email("a@higashikagawa.jp")
    assert not is_gov_email("a@gmail.com")


def test_phone_flagged_but_postal_and_whitelist_ok() -> None:
    policy = default_jp_gov_policy()
    assert _kinds("お問い合わせ 090-1234-5678", policy=policy) == ["phone"]
    assert _kinds("代表 0268-62-1111", policy=policy) == ["phone"]
    # 郵便番号（2 グループ）は電話として検出しない
    assert scan_text("〒390-0852 松本市", policy=policy) == []
    # ホワイトリスト登録した代表電話は許可
    allowed = default_jp_gov_policy(allow_phones=["0268-62-1111"])
    assert scan_text("代表 0268-62-1111", policy=allowed) == []


def test_fullwidth_digits_normalized() -> None:
    policy = default_jp_gov_policy()
    assert _kinds("電話　０９０ー１２３４ー５６７８", policy=policy) == ["phone"]


def test_name_detection_and_stoplist() -> None:
    policy = default_jp_gov_policy()
    assert _kinds("担当は山田様です", policy=policy) == ["name"]
    assert _kinds("所有者: 佐藤一郎", policy=policy) == ["name"]
    # 一般語は氏名として扱わない
    assert scan_text("皆様のご来場をお待ちしています", policy=policy) == []
    assert scan_text("お客様各位", policy=policy) == []


def test_allow_also_whitelists_operator_contact() -> None:
    base = default_jp_gov_policy()
    policy = allow_also(base, emails=["support@akiya-atlas.example"], phones=["03-1234-5678"])
    assert scan_text("support@akiya-atlas.example", policy=policy) == []
    assert scan_text("03-1234-5678", policy=policy) == []
    # 別のメールは依然として検出
    assert _kinds("other@akiya-atlas.example", policy=policy) == ["email"]


def test_normalize_phone() -> None:
    assert normalize_phone("０２６８ー６２ー１１１１") == "0268621111"
    assert normalize_phone("(0268) 62-1111") == "0268621111"


def test_compound_words_with_honorific_chars_are_not_names() -> None:
    policy = default_jp_gov_policy()
    # 「様式」「氏名」は敬称ではない
    assert scan_text("別紙様式を提出してください。申請者氏名を記入。", policy=policy) == []
    assert _kinds("担当は山田様です", policy=policy) == ["name"]
