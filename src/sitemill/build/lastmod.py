"""サイトマップの lastmod を「ページの中身が最後に変わった日」にする（ADR 0025）。

ビルドの時刻や巡回の時刻を lastmod にすると、毎日全ページが「今日変わった」ことになる。
Google はそういう lastmod を信用しなくなり、本当に変わったページを先にクロールしてもらう手がかりが
消える（2026-09-17 時点で japan-open-today は 891 件すべて、
akiya-atlas は 6,421 件中 6,390 件が「今日」）。

やり方: ページの `<main>` の中身から指紋を作り、前回のビルドと比べる。変わっていれば今日、
変わっていなければ前回の日付のまま。指紋と日付は `data/state/lastmod.json` に残してコミットする。

指紋に入れないもの:
- `<main>` の外（信頼シグナルの「最終更新」「取得日時」、ヘッダ・フッタ）
- `data-sitemill-volatile` を付けた要素。**日付から決まる表示**（「9 月 17 日は開館」、週の帯、
  今日開いている施設の一覧）に付ける。事実ではなく、その日に描いた結果なので
- 並び順。行を並べ替えてから指紋にするので、日ごとに並びが変わる一覧は付けなくてよい

付け忘れは実行レポートで分かる。`build.lastmod_changed` が毎日ほぼ全ページなら、
日付で変わる表示に印が付いていない。

**見た目を変えた回は日付を据え置く。** 指紋はテンプレートが描いた HTML から作るので、
テンプレートを変えると全ページの指紋が変わる。台帳にテンプレートの鍵（`layout_key`）を残し、
鍵が変わった回は指紋を作り直して日付は動かさない（`build.lastmod_relearned`）。事実が変わった
ページも据え置くことになるが、その変化は次の実行で拾える。全ページを「今日」にするより害が小さい。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from selectolax.parser import HTMLParser

VOLATILE_ATTR = "data-sitemill-volatile"
# 指紋の作り方を変えたら上げる。上げた次のビルドは、指紋を作り直して日付を据え置く
FINGERPRINT_VERSION = 1
# 項目の始まりの前と終わりの後で行を切る
_ITEM = r"(?:li|tr|dt|dd|p|div|section|article)"
_ITEM_START = re.compile(r"(<" + _ITEM + r"\b[^>]*>)", re.I)
_ITEM_END = re.compile(r"(</" + _ITEM + r">)", re.I)


def fingerprint(html: str) -> str:
    """`<main>` から日付で変わる要素を除き、行を並べ替えて作る指紋。"""
    tree = HTMLParser(html)
    main = tree.css_first("main") or tree.body
    if main is None:
        return ""
    for node in main.css(f"[{VOLATILE_ATTR}]"):
        node.decompose()
    # 項目の終わりで行を切ってから並べ替える。1 行に並んだ一覧でも、並び順だけの違いにならない
    text = _ITEM_END.sub(r"\1\n", _ITEM_START.sub(r"\n\1", main.html or ""))
    lines = sorted(line.strip() for line in text.splitlines() if line.strip())
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# sitemill 自身のテンプレート（サービスが差し替えていない部品）も見た目を決める
_PACKAGE_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def layout_key(*template_dirs: Path) -> str:
    """テンプレートと指紋の作り方から作る鍵。見た目を変えると変わる。

    指紋はテンプレートが描いた HTML から作るので、**テンプレートを変えると全ページの指紋が変わる**。
    そのまま比べると、翌日の実行で全ページが「今日変わった」になり、lastmod の意味が消える
    （akiya-atlas は取得時刻に印を付けただけで 6,547 ページ中 5,717 ページの指紋が変わった）。
    鍵が変われば「見た目が変わった日」と分かるので、指紋を作り直して日付は据え置く。
    """
    h = hashlib.sha256(f"v{FINGERPRINT_VERSION}\n".encode())
    for base in (*template_dirs, _PACKAGE_TEMPLATES):
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            h.update(str(path.relative_to(base)).replace("\\", "/").encode("utf-8"))
            h.update(b"\n")
            h.update(path.read_bytes())
            h.update(b"\n")
    return "sha256:" + h.hexdigest()


@dataclass
class LastmodLedger:
    """ページのパス → (指紋, 中身が最後に変わった日)。"""

    path: Path
    pages: dict[str, dict[str, str]] = field(default_factory=dict)
    layout: str = ""  # いまの見た目の鍵。台帳に残っているものと違えば作り直す
    relearn: bool = False  # 見た目が変わった。指紋を作り直して日付は据え置く
    changed: int = 0  # 中身が変わって今日になった
    added: int = 0  # 初めて見たページ
    kept: int = 0  # 変わらず前回の日付のまま
    relearned: int = 0  # 見た目が変わったので、指紋だけ作り直して日付は据え置いた
    _seen: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path, *, layout: str = "") -> LastmodLedger:
        pages: dict[str, dict[str, str]] = {}
        stored = ""
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            pages = dict(data.get("pages", {}))
            stored = str(data.get("layout", ""))
        # 台帳に鍵が無い（この仕組みより前に作った）ときは作り直さない。鍵を書き足すだけ
        relearn = bool(layout) and bool(stored) and stored != layout
        return cls(path=path, pages=pages, layout=layout, relearn=relearn)

    def update(self, page_path: str, html: str, *, today: date, first_seen: date) -> date:
        """今回のビルドでのそのページの lastmod を返す。

        `first_seen` は初めて見たページの日付。サービスが事実の日付を持っていればそれを、
        無ければ今日を渡す（今日より先の日付にはしない）。
        """
        self._seen.add(page_path)
        digest = fingerprint(html)
        known = self.pages.get(page_path)
        if known is None:
            day = min(first_seen, today)
            self.added += 1
        elif known.get("fingerprint") == digest:
            self.kept += 1
            return date.fromisoformat(known["lastmod"])
        elif self.relearn:
            # 見た目が変わった回は、事実が変わったページも見分けられない。日付は据え置く
            # （事実の変化は次の実行で拾う）。進める側に倒すと全ページが今日になる
            day = date.fromisoformat(known["lastmod"])
            self.relearned += 1
        else:
            day = today
            self.changed += 1
        self.pages[page_path] = {"fingerprint": digest, "lastmod": day.isoformat()}
        return day

    def save(self) -> Path:
        """今回ビルドしたページだけを残す（消えたページの日付を持ち越さない）。"""
        kept = {p: v for p, v in sorted(self.pages.items()) if p in self._seen}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"layout": self.layout, "pages": kept}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return self.path
