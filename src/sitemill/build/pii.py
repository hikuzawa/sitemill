"""個人情報（氏名・電話番号・メールアドレス）の検出。公開前検査に使う（ADR 0012）。

方針:
- 自治体・官公庁の代表メール（.lg.jp / .go.jp / 地理型 city.*/town.*/vill.* 等）と、
  明示的にホワイトリスト登録した代表電話は許可する（PiiPolicy）。
- それ以外の電話番号・メール・氏名らしき文字列を検出する。
- 誤検出でビルドを不必要に止めないよう、高精度なパターンに絞る。
- 全角の数字・記号は半角化してから走査する。
本文は「データ」であり、ここで探すのは公開してはいけない個人情報だけ。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# 全角→半角（数字・＠・．）。ダッシュ類は電話用の正規化でまとめて '-' にする。
_Z2H = {ord(z): h for z, h in zip("０１２３４５６７８９＠．（）", "0123456789@.()", strict=True)}
_DASHES = "－ー―‐‑–—−"  # 全角ハイフン・長音・各種ダッシュ

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 日本の電話番号: 0 始まり 3 グループ（0AB-CDE-FGHI 等）か、括弧市外局番形式。
# 郵便番号（390-0852 の 2 グループ）は 2 つのダッシュを要求することで除外する。
PHONE_RE = re.compile(r"(?<![\d-])(?:0\d{1,4}-\d{1,4}-\d{4}|\(0\d{1,4}\)\s?\d{1,4}-\d{4})(?![\d-])")
# 氏名らしき語 + 敬称。氏名は漢字 2〜4 文字に限定し、「様式」「氏名」のような熟語は除く
# （仕様・同様・皆様・別紙様式・申請者氏名 などを誤検出しない）。
HONORIFIC_RE = re.compile(r"([一-龥]{2,4})(様(?!式)|さん|氏(?!名))")
# 「担当者: ◯◯」のようなラベル付き氏名。
LABEL_RE = re.compile(
    r"(氏名|お名前|ご担当者?|担当者|所有者|売主|貸主|貸主氏名|代表者|世帯主)\s*[:：]\s*"
    r"([^\s、。,.<>「」【】\r\n]{1,24})"
)
_NAME_STOP = {
    "皆様",
    "皆さん",
    "お客様",
    "各位",
    "関係者",
    "利用者",
    "訪問者",
    "来訪者",
    "神様",
    "王様",
    "殿様",
    "奥様",
    "旦那様",
    "地域",
    "個人",
    "法人",
    "未定",
    "非公開",
}
_MUNI_EMAIL_RE = re.compile(
    r"(?:^|\.)(?:city|town|vill|village|pref|metro|city-office)\.[a-z0-9.\-]+\.jp$", re.I
)


def normalize_for_scan(text: str) -> str:
    """全角数字・＠・．を半角化する（ダッシュはそのまま。電話用に別途正規化する）。"""
    return text.translate(_Z2H)


def _normalize_phone_text(text: str) -> str:
    out = text.translate(_Z2H)
    for d in _DASHES:
        out = out.replace(d, "-")
    return out


def normalize_phone(value: str) -> str:
    """比較用に電話番号から数字だけを取り出す。"""
    return re.sub(r"\D", "", _normalize_phone_text(value))


def find_phones(text: str) -> list[str]:
    """テキストから電話番号らしき文字列を抜き出す。代表電話の収集に使う。"""
    return [m.group(0) for m in PHONE_RE.finditer(_normalize_phone_text(text))]


def is_gov_email(email: str) -> bool:
    """官公庁・自治体ドメインの代表メールか（.lg.jp / .go.jp / 地理型自治体ドメイン）。"""
    domain = email.rsplit("@", 1)[-1].lower().rstrip(".")
    if domain.endswith((".lg.jp", ".go.jp")):
        return True
    return bool(_MUNI_EMAIL_RE.search(domain))


@dataclass(frozen=True)
class PiiPolicy:
    """検出結果のうち許可するものを判定する。既定では何も許可しない。"""

    allow_email: Callable[[str], bool] = lambda _e: False
    allow_phone: Callable[[str], bool] = lambda _p: False


def default_jp_gov_policy(allow_phones: Iterable[str] = ()) -> PiiPolicy:
    """自治体・官公庁の代表メールを許可し、明示登録した代表電話だけ許可するポリシー。"""
    allowed = {normalize_phone(p) for p in allow_phones}
    return PiiPolicy(
        allow_email=is_gov_email,
        allow_phone=lambda p: normalize_phone(p) in allowed,
    )


def allow_also(
    policy: PiiPolicy, *, emails: Iterable[str] = (), phones: Iterable[str] = ()
) -> PiiPolicy:
    """既存ポリシーに、明示的に許可するメール・電話を足した新しいポリシーを返す。

    運営者自身の連絡先など、第三者の個人情報ではないものを許可するのに使う。
    """
    es = {e.lower() for e in emails}
    ps = {normalize_phone(p) for p in phones}
    return PiiPolicy(
        allow_email=lambda e: e.lower() in es or policy.allow_email(e),
        allow_phone=lambda p: normalize_phone(p) in ps or policy.allow_phone(p),
    )


@dataclass(frozen=True)
class PiiFinding:
    kind: str  # "email" | "phone" | "name"
    text: str
    snippet: str
    where: str = ""

    def describe(self) -> str:
        loc = f"{self.where}: " if self.where else ""
        label = {"email": "メールアドレス", "phone": "電話番号", "name": "氏名らしき語"}[self.kind]
        return f"{loc}{label} 「{self.text}」（…{self.snippet}…）"


def _snippet(text: str, start: int, end: int, pad: int = 16) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def scan_text(text: str, *, policy: PiiPolicy | None = None, where: str = "") -> list[PiiFinding]:
    """テキストから個人情報らしき文字列を探す。許可済みのものは除く。"""
    if not text:
        return []
    policy = policy or PiiPolicy()
    findings: list[PiiFinding] = []
    scan = normalize_for_scan(text)

    for m in EMAIL_RE.finditer(scan):
        email = m.group(0)
        if not policy.allow_email(email):
            findings.append(PiiFinding("email", email, _snippet(scan, m.start(), m.end()), where))

    phone_scan = _normalize_phone_text(text)
    for m in PHONE_RE.finditer(phone_scan):
        phone = m.group(0)
        if not policy.allow_phone(phone):
            findings.append(
                PiiFinding("phone", phone, _snippet(phone_scan, m.start(), m.end()), where)
            )

    for m in HONORIFIC_RE.finditer(scan):
        name, whole = m.group(1), m.group(0)
        if name in _NAME_STOP or whole in _NAME_STOP:
            continue
        findings.append(PiiFinding("name", whole, _snippet(scan, m.start(), m.end()), where))
    for m in LABEL_RE.finditer(scan):
        value = m.group(2).strip()
        if value and value not in _NAME_STOP:
            snippet = _snippet(scan, m.start(), m.end())
            findings.append(PiiFinding("name", f"{m.group(1)}: {value}", snippet, where))
    return findings


@dataclass
class PiiReport:
    findings: list[PiiFinding] = field(default_factory=list)

    def extend(self, more: Iterable[PiiFinding]) -> None:
        self.findings.extend(more)

    @property
    def ok(self) -> bool:
        return not self.findings

    def messages(self, limit: int = 10) -> list[str]:
        return [f.describe() for f in self.findings[:limit]]
