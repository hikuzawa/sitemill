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


@dataclass
class LastmodLedger:
    """ページのパス → (指紋, 中身が最後に変わった日)。"""

    path: Path
    pages: dict[str, dict[str, str]] = field(default_factory=dict)
    changed: int = 0  # 中身が変わって今日になった
    added: int = 0  # 初めて見たページ
    kept: int = 0  # 変わらず前回の日付のまま
    _seen: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> LastmodLedger:
        pages: dict[str, dict[str, str]] = {}
        if path.is_file():
            pages = dict(json.loads(path.read_text(encoding="utf-8")).get("pages", {}))
        return cls(path=path, pages=pages)

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
        elif known.get("fingerprint") != digest:
            day = today
            self.changed += 1
        else:
            self.kept += 1
            return date.fromisoformat(known["lastmod"])
        self.pages[page_path] = {"fingerprint": digest, "lastmod": day.isoformat()}
        return day

    def save(self) -> Path:
        """今回ビルドしたページだけを残す（消えたページの日付を持ち越さない）。"""
        kept = {p: v for p, v in sorted(self.pages.items()) if p in self._seen}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"pages": kept}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return self.path
