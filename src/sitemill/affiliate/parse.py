"""ASP の検索結果テキストを案件に分ける（ADR 0019）。

ASP ごとに画面は違うが、貼り付けたテキストはどれも「ラベル + 値」の並びになる。
ここはラベルの異名表だけを持ち、値は `parse/jp` の決定的パーサで数値にする（quote-then-parse）。
LLM は使わない。同じテキストを貼れば必ず同じ表が出る。

分け方:
- 空行は必ず案件の切れ目にする（うまく分かれないときは案件の間に空行を入れれば確実）
- 空行が無い貼り付けでは、「ラベル付きの行が続いたあとに現れたラベルの無い行」を
  次の案件の見出しとみなす
- ラベルらしく見えても値が項目の形（金額・％・日数など）になっていなければラベルとして扱わない
  （「提携先の会社」の「提携」などを誤ってラベルにしないため）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sitemill.affiliate.models import Candidate
from sitemill.parse.jp import normalize_text, parse_int, parse_percent, parse_yen

# 項目 → ASP ごとの呼び名。長いものから当てるので、包含関係があっても取り違えない
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("プログラム名", "案件名", "広告名", "商品名"),
    "advertiser": ("広告主名", "広告主", "マーチャント", "企業名", "会社名", "出稿企業"),
    "asp": ("ASP", "提供元"),
    "reward": (
        "アフィリエイト報酬",
        "成果報酬",
        "報酬単価",
        "報酬額",
        "基本報酬",
        "報酬",
        "単価",
    ),
    "condition": (
        "成果発生条件",
        "成果条件",
        "成果地点",
        "承認条件",
        "報酬条件",
        "成果ポイント",
    ),
    "approval_rate": ("成果承認率", "確定率", "承認率"),
    "epc": ("EPC", "クリック単価"),
    "cookie": ("Cookie有効期間", "クッキー有効期間", "再訪問期間", "クッキー期間", "cookie"),
    "review": ("提携審査", "提携申請", "提携状況", "審査", "提携"),
    "region": ("対応エリア", "対応地域", "対象エリア", "対象地域", "サービスエリア", "エリア"),
}
_ALIAS_TO_FIELD = {a.lower(): f for f, aliases in _FIELD_ALIASES.items() for a in aliases}
_ALIASES_BY_LENGTH = sorted(_ALIAS_TO_FIELD, key=len, reverse=True)
_LABEL_RE = re.compile(
    r"(?:^|(?<=[\s|｜/／,、･・（(\[【]))"
    r"(" + "|".join(re.escape(a) for a in _ALIASES_BY_LENGTH) + r")"
    r"\s*[:：]?[\s]*",
    re.IGNORECASE,
)
_SEP = " 　|｜/／,、:：-–—"

# 案件と関係のない画面の部品。ラベルを探す前に捨てる
_NOISE = re.compile(
    r"\d+"
    r"|[／/|｜・\-–—=＝*＊★☆▼▲><]+"
    r"|検索結果|検索条件|並び替え|絞り込み|新着順|人気順|おすすめ順|該当件数"
    r"|\d+\s*件(?:中.*)?|\d+\s*/\s*\d+|ページ\s*\d+|前へ|次へ|もっと見る|さらに表示"
    r"|お気に入り(?:に追加|登録)?|詳細を見る|プログラム詳細|提携する|広告リンク作成"
)
# 前の行の続き（値が折り返された行）
_CONTINUATION = re.compile(r"^[※＊*・･\-–—」）)\]】>＞]|^(?:および|ならびに|かつ|ただし|なお)")

# 地域制限として読む語。都道府県は 47 件すべてを持つ（住所の一部と見分けるため文脈語と併用する）
PREFECTURES: tuple[str, ...] = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)  # fmt: skip
_BLOCK_WORDS = (
    "首都圏", "一都三県", "一都六県", "関東", "関西", "近畿", "東海", "中部",
    "東北", "北陸", "甲信越", "中国地方", "四国", "九州", "都市部", "全国",
)  # fmt: skip
_AREA_WORDS = tuple(sorted((*PREFECTURES, *_BLOCK_WORDS), key=len, reverse=True))
_AREA_RE = re.compile("|".join(re.escape(w) for w in _AREA_WORDS))
# 地名の近くにこれがあるときだけ「地域の制限」として読む（広告主の住所と見分ける）
_REGION_CTX = re.compile(r"エリア|地域|限定|対応|対象|在住|お住まい|のみ|除く|を含む|近郊|圏内")
_REGION_WINDOW = 12

_IMMEDIATE = re.compile(r"即時|自動|無審査|提携済|提携中")
_NEEDS_REVIEW = re.compile(r"審査|承認制|要申請|申請が必要|未提携|提携申請")


@dataclass
class _Block:
    free: list[str] = field(default_factory=list)  # ラベルの無い行（見出し・広告主）
    fields: dict[str, str] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.free and not self.fields


def parse_offers(text: str, *, asp: str = "") -> list[Candidate]:
    """貼り付けたテキストを案件の並びにする。読めなかった項目は None のままにする。"""
    out: list[Candidate] = []
    for block in _split_blocks(text):
        cand = _candidate(block, asp=asp)
        if cand is not None:
            out.append(cand)
    return out


def _split_blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    cur = _Block()

    def flush() -> None:
        nonlocal cur
        if not cur.empty:
            blocks.append(cur)
        cur = _Block()

    for raw in text.splitlines():
        line = normalize_text(raw)
        if not line:
            flush()
            continue
        if _NOISE.fullmatch(line):
            continue
        pairs, head = _line_fields(line)
        if head and cur.fields and not _CONTINUATION.search(head):
            flush()  # ラベル付きの行のあとの見出し = 次の案件
        if head:
            cur.free.append(head)
        for name, value in pairs:
            cur.fields.setdefault(name, value)  # 同じ項目が二度出たら先に出た方
        cur.lines.append(line)
    flush()
    return blocks


def _line_fields(line: str) -> tuple[list[tuple[str, str]], str]:
    """1 行を (項目, 値) の並びと、ラベルの無い先頭部分に分ける。"""
    matches = list(_LABEL_RE.finditer(line))
    if not matches:
        return [], line.strip(_SEP)
    head = line[: matches[0].start()].strip(_SEP)
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(matches):
        m = matches[i]
        name = _ALIAS_TO_FIELD[m.group(1).lower()]
        # 値が空なら、続くラベルらしき語も値の一部とみなして取り込む
        # （「提携申請 審査あり」の「審査」を別のラベルにしないため）
        j = i + 1
        end = matches[j].start() if j < len(matches) else len(line)
        value = line[m.end() : end].strip(_SEP)
        while not value and j < len(matches):
            j += 1
            end = matches[j].start() if j < len(matches) else len(line)
            value = line[m.end() : end].strip(_SEP)
        i = j
        if _plausible(name, value):
            pairs.append((name, value))
            continue
        # ラベルに見えただけ。切らずに直前の値（無ければ見出し）へ戻す
        text = f"{m.group(0)}{value}".strip()
        if pairs:
            pairs[-1] = (pairs[-1][0], f"{pairs[-1][1]} {text}".strip())
        else:
            head = f"{head} {text}".strip(_SEP)
    return pairs, head


def _plausible(name: str, value: str) -> bool:
    """その項目の値として筋が通るか。通らなければラベルとして扱わない。"""
    if not value:
        return False
    if name in ("reward", "epc"):
        return bool(re.search(r"\d", value))
    if name == "approval_rate":
        return parse_percent(value) is not None
    if name == "cookie":
        return bool(re.search(r"\d", value))
    if name == "review":
        return bool(_IMMEDIATE.search(value) or _NEEDS_REVIEW.search(value))
    if name == "region":
        return bool(_AREA_RE.search(value))
    if name == "condition":
        return len(value) >= 4
    return True


def _candidate(block: _Block, *, asp: str) -> Candidate | None:
    f = block.fields
    raw = "\n".join(block.lines)
    name = f.get("name") or (block.free[0] if block.free else "")
    if not name and not f.get("reward"):
        return None  # 案件の体をなしていない

    advertiser = f.get("advertiser") or (block.free[1] if len(block.free) > 1 else "")
    reward_quote = f.get("reward", "")
    reward_yen, _ = parse_yen(reward_quote)
    reward_rate = parse_percent(reward_quote)
    epc_yen, _ = parse_yen(f.get("epc", ""))
    if epc_yen is None and f.get("epc"):
        epc_yen = parse_int(f["epc"])

    notes: list[str] = []
    if not reward_quote:
        notes.append("報酬が読めなかった")
    if not f.get("condition"):
        notes.append("成果条件が読めなかった")
    if f.get("approval_rate") is None:
        notes.append("確定率の記載なし")
    if f.get("epc") is None:
        notes.append("EPC の記載なし")

    return Candidate(
        name=name or raw[:40],
        asp=f.get("asp") or asp,
        advertiser=advertiser,
        reward_quote=reward_quote,
        reward_yen=reward_yen,
        reward_rate=reward_rate if reward_yen is None else None,
        condition=f.get("condition", ""),
        approval_rate=parse_percent(f.get("approval_rate", "")),
        epc_yen=epc_yen,
        cookie_days=parse_int(f.get("cookie", "")) if f.get("cookie") else None,
        review_required=_review_required(f.get("review", "")),
        region_quotes=region_quotes(raw, f.get("region", "")),
        raw=raw,
        notes=tuple(notes),
    )


def _review_required(value: str) -> bool | None:
    if not value:
        return None
    if _IMMEDIATE.search(value):
        return False
    if _NEEDS_REVIEW.search(value):
        return True
    return None


def region_quotes(text: str, declared: str = "") -> tuple[str, ...]:
    """地域の制限として読める部分を原文のまま抜き出す。

    「対応エリア」などのラベルで明示されていればその値を使う。無ければ本文を走査するが、
    広告主の所在地（「東京都渋谷区…」）を制限と取り違えないよう、地名の周辺に
    「エリア」「限定」「対応」などの文脈語がある場合だけ拾い、重なる範囲は 1 つにまとめる。
    """
    if declared and _AREA_RE.search(declared):
        return (normalize_text(declared),)
    t = normalize_text(text)
    spans: list[list[int]] = []
    for m in _AREA_RE.finditer(t):
        left = max(0, m.start() - _REGION_WINDOW)
        right = min(len(t), m.end() + _REGION_WINDOW)
        if not _REGION_CTX.search(t[left:right]):
            continue
        if spans and left <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], right)
        else:
            spans.append([left, right])
    return tuple(t[a:b].strip() for a, b in spans)
