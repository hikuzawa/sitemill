"""ASP の検索結果テキストを案件に分ける（ADR 0019）。

ASP ごとに画面は違うが、貼り付けたテキストはどれも「見出し（案件名・広告主）＋ 項目の並び」になる。
ここはラベルの異名表だけを持ち、値は `parse/jp` の決定的パーサで数値にする（quote-then-parse）。
LLM は使わない。同じテキストを貼れば必ず同じ表が出る。

読み方（A8 の実データ 40 件で確かめた。ADR 0022）:

- **空行は案件の切れ目にしない**。実際の画面では案件の内側に空行が入る（A8 は 3 行連続）。
  切りたいところに `---` の行を入れれば、そこで必ず切れる
- **案件の切れ目は「同じ項目が二度目に出たところ」**。ASP の一覧は同じ項目を同じ順に繰り返すので、
  すでに読んだ項目がまた出てきたら次の案件が始まっている
- **ラベルと値は同じ行にあるとは限らない**。ラベルだけの行の次に値が来る縦並びも読む
  （A8 の「成果報酬」「EPC」「確定率」）。値が複数行に分かれていれば、
  項目の形（金額・％・日数）になるまでつなぐ
- **「-」だけの行は「記載なし」**として食べる。値が無いことと、読めなかったことを混ぜない
- **案件名と広告主は「最初の項目の直前 2 行」だけ**を使う。それより前は一覧の画面部品
  （検索条件・ページ送り・「広告サンプル」「プログラム詳細を見る」など）なので捨てる。
  この 2 行だけを原文（`raw`）に入れるので、前の案件や画面の文言が判定に混ざらない
- ラベルらしく見えても値が項目の形になっていなければラベルとして扱わない
  （「提携先の会社」の「提携」を提携状況のラベルにしないため）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sitemill.affiliate.models import Candidate
from sitemill.parse.jp import find_numbers, normalize_text, parse_int, parse_percent, parse_yen

# 項目 → ASP ごとの呼び名。長いものから当てるので、包含関係があっても取り違えない
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("プログラム名", "案件名", "広告名", "商品名"),
    "advertiser": ("広告主名", "広告主", "マーチャント", "企業名", "会社名", "出稿企業"),
    "asp": ("ASP", "提供元"),
    "reward": (
        "アフィリエイト報酬",
        "成果報酬額",
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
_ALIAS_PATTERN = "|".join(re.escape(a) for a in _ALIASES_BY_LENGTH)
_LABEL_RE = re.compile(
    r"(?:^|(?<=[\s|｜/／,、･・（(\[【]))"
    r"(" + _ALIAS_PATTERN + r")"
    # ラベルの直後に文字が続くなら、それは別の語（「広告主サイト」の「広告主」）
    r"(?![ぁ-んァ-ヴ一-龥ー])"
    r"\s*[:：]?[\s]*",
    re.IGNORECASE,
)
# ラベルだけの行（次の行に値が来る縦並び）。「成果報酬（税込）:」のような飾りは許す
_LONE_LABEL_RE = re.compile(
    r"(" + _ALIAS_PATTERN + r")\s*(?:[（(][^）)]*[）)])?\s*[:：]?",
    re.IGNORECASE,
)
_SEP = " 　|｜/／,、:：-–—"

# 見出しとして使う行数。案件名と広告主の 2 行だけを使い、それより前の画面部品は捨てる
_HEAD_LINES = 2
# 縦並びの値を何行までつなぐか。これを超えても項目の形にならなければ「読めなかった」にする
_VALUE_LINES = 3

# 案件と関係のない画面の部品。ラベルを探す前に捨てる
_NOISE = re.compile(
    r"\d+"
    r"|[／/|｜・\-–—=＝*＊★☆▼▲><]+"
    r"|検索結果|検索条件|並び替え|絞り込み|新着順|人気順|おすすめ順|該当件数"
    r"|\d+\s*件(?:中.*)?|\d+\s*/\s*\d+|ページ\s*\d+|前へ|次へ|もっと見る|さらに表示"
    r"|お気に入り(?:に追加|登録)?|詳細を見る|プログラム詳細|提携する|広告リンク作成"
)
# 貼り付ける人が入れる、案件の切れ目
_SEPARATOR = re.compile(r"[-–—―=＝_]{3,}")
# 「記載なし」の印。値が無いことと、読めなかったことを混ぜない
_NO_VALUE = re.compile(r"[-–—―ー−‐]+|なし|未定|非公開|不明|準備中")
# ラベルの無い提携状況（A8 は「未提携」だけが 1 行で出る）
_STATUS = re.compile(r"未提携|提携申請中|提携中|提携済み?|申請中|審査中|審査待ち|即時提携|提携可能")
# 成果報酬の欄に条件と金額が同居する ASP がある（A8「新規査定申込20000円」）。金額を外した残りが条件
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:億|万|千)?\s*(?:円|%|ポイント|pt)?")
_HAS_WORD = re.compile(r"[一-龥ぁ-んァ-ヴー]")

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

_IMMEDIATE = re.compile(r"即時|自動|無審査|提携済|提携中|提携可能")
_NEEDS_REVIEW = re.compile(r"審査|承認制|要申請|申請が必要|申請中|未提携|提携申請")


@dataclass
class _Record:
    """1 案件分の行。`head` は見出し（案件名と広告主）で、最大 `_HEAD_LINES` 行。"""

    head: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)  # 値が読めなくても「出てきた」項目
    lines: list[str] = field(default_factory=list)  # 原文（見出し + この案件の行）


def parse_offers(text: str, *, asp: str = "") -> list[Candidate]:
    """貼り付けたテキストを案件の並びにする。読めなかった項目は None のままにする。"""
    out: list[Candidate] = []
    for record in _split_records(text):
        cand = _candidate(record, asp=asp)
        if cand is not None:
            out.append(cand)
    return out


def _split_records(text: str) -> list[_Record]:
    """同じ項目が二度目に出たところで切る。ラベル無しの行は次の案件の見出し候補にする。"""
    lines = [normalize_text(raw) for raw in text.splitlines()]
    records: list[_Record] = []
    cur = _Record()
    pending: list[str] = []  # 直近の、ラベルの無い行の連なり

    def start_new(*, hand_over: bool = True) -> None:
        """今の案件を閉じ、直前のラベル無し行の末尾を次の案件の見出しとして渡す。

        最後の項目より後ろの行は、見出しに使う 2 行を除いてどちらの案件にも入れない。
        一覧の画面部品（「広告サンプル」・ページ送り・次ページのヘッダ）と、
        区切りの無い関連キーワードの塊が混ざっているため。
        """
        nonlocal cur, pending
        if cur.seen:
            records.append(cur)
        head = pending[-_HEAD_LINES:] if hand_over else []
        cur = _Record(head=head, lines=list(head))
        pending = []

    i = 0
    while i < len(lines):
        start = i
        line = lines[i]
        i += 1
        if not line or _NOISE.fullmatch(line):
            continue
        if _SEPARATOR.fullmatch(line):
            start_new(hand_over=False)  # 貼る人が入れた区切り。手前の行は前の案件のもの
            continue
        pairs, head, i = _read_line(lines, start)
        if head:
            pending.append(head)
        if not pairs:
            continue
        for name, value in pairs:
            if name in cur.seen:
                start_new()  # 同じ項目が二度目 = 次の案件
            elif pending:
                if cur.head:
                    cur.lines += pending  # 項目と項目の間の行は原文に残す
                else:
                    # 見出しはここ。それより前は一覧の画面部品なので原文にも入れない
                    cur.head = pending[-_HEAD_LINES:]
                    cur.lines = list(cur.head)
                pending = []
            cur.seen.add(name)
            if value:
                cur.fields.setdefault(name, value)  # 同じ項目が二度出たら先に出た方
        cur.lines += [ln for ln in lines[start:i] if ln]
    start_new(hand_over=False)  # 最後の案件を閉じる
    return records


def _read_line(lines: list[str], i: int) -> tuple[list[tuple[str, str]], str, int]:
    """1 行を読む。縦並びなら値の行も食べて、次に読む行の位置を返す。"""
    line = lines[i]
    lone = _LONE_LABEL_RE.fullmatch(line)
    if lone:
        name = _ALIAS_TO_FIELD[lone.group(1).lower()]
        value, nxt = _read_value(lines, i + 1, name)
        return [(name, value)], "", nxt
    if _STATUS.fullmatch(line):
        return [("review", line)], "", i + 1
    pairs, head = _line_fields(line)
    return pairs, head, i + 1


def _read_value(lines: list[str], i: int, name: str) -> tuple[str, int]:
    """ラベルだけの行の次から値を読む。読めなければ行を食べずに返す（自由行として残す）。"""
    parts: list[str] = []
    j = i
    while j < len(lines) and len(parts) < _VALUE_LINES:
        line = lines[j]
        if not line:
            j += 1  # 値の途中に空行が入る画面がある（A8 の段組み報酬）
            continue
        if _SEPARATOR.fullmatch(line) or _LONE_LABEL_RE.fullmatch(line) or _STATUS.fullmatch(line):
            break
        if _line_fields(line)[0]:
            break  # 次の項目が始まった
        if not parts and _NO_VALUE.fullmatch(line):
            return "", j + 1  # 「-」= 記載なし。行だけ食べて値は空のまま
        parts.append(line)
        j += 1
        joined = " ".join(parts)
        if _plausible(name, joined):
            return joined, j
    return "", i


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


def _candidate(record: _Record, *, asp: str) -> Candidate | None:
    f = record.fields
    raw = "\n".join(record.lines)
    heading = record.head
    name = f.get("name") or (heading[0] if heading else "")
    if not name and not f.get("reward"):
        return None  # 案件の体をなしていない

    advertiser = f.get("advertiser") or (heading[1] if len(heading) > 1 else "")
    reward_quote = f.get("reward", "")
    reward_yen, _ = parse_yen(reward_quote)
    reward_rate = parse_percent(reward_quote)
    condition = f.get("condition") or _condition_from_reward(reward_quote)

    notes: list[str] = []
    if not reward_quote:
        notes.append("報酬が読めなかった")
    if not condition:
        notes.append("成果条件が読めなかった")
    if "approval_rate" not in f:
        notes.append("確定率の記載なし")
    if "epc" not in f:
        notes.append("EPC の記載なし")

    return Candidate(
        name=name or raw[:40],
        asp=f.get("asp") or asp,
        advertiser=advertiser,
        reward_quote=reward_quote,
        reward_yen=reward_yen,
        reward_rate=reward_rate if reward_yen is None else None,
        condition=condition,
        approval_rate=parse_percent(f.get("approval_rate", "")),
        epc_yen=_epc(f.get("epc", "")),
        cookie_days=parse_int(f.get("cookie", "")) if f.get("cookie") else None,
        review_required=_review_required(f.get("review", "")),
        region_quotes=region_quotes(raw, f.get("region", "")),
        raw=raw,
        notes=tuple(notes),
    )


def _epc(quote: str) -> float | None:
    """EPC を円で返す。ASP は小数で出す（A8 は「56.3」）ので整数に丸めない。"""
    if not quote:
        return None
    yen, _ = parse_yen(quote)
    if yen is not None:
        return float(yen)
    nums = find_numbers(quote)
    return float(nums[0]) if nums else None


def _condition_from_reward(quote: str) -> str:
    """成果報酬の欄から金額を除いた残りを成果条件の原文として使う。

    A8 は「新規査定申込20000円」のように条件と金額を同じセルに入れる。金額を外した残りは
    原文の一部なので、推定ではなく引用として扱える（quote-then-parse、ADR 0004）。
    """
    rest = normalize_text(_AMOUNT.sub(" ", quote)).strip(_SEP)
    if len(rest) < 2 or not _HAS_WORD.search(rest):
        return ""
    return rest


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
