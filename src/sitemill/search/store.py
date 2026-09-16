"""Search Console の取得結果をリポジトリに残す（ADR 0023）。

置き場所（サービスの `data/search/`）:

    performance/<粒度>/<YYYY-MM>.jsonl   検索パフォーマンス。粒度は query / page / page_query
    index/urls.json                      URL 検査の結果（URL ごとに最後の 1 回）
    index/states.json                    状態ごとの件数を日ごとに 1 行（週の比較に使う）
    index/variants.json                  www・http の形の URL 検査（転送の確認）
    index/sitemaps.json                  サイトマップの状態（取得のたびに追記）

**日付ごとに上書きする**。Search Console の数字は 2〜3 日遅れて確定するので、毎日「直近 5 日」を
取り直し、同じ日の行があれば新しい方で置き換える。取り直しても行が増えるだけにならないよう、
書き込みは月ファイル単位でまとめて行う。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 粒度。API の dimensions と 1 対 1
GRAINS: dict[str, tuple[str, ...]] = {
    "query": ("date", "query"),
    "page": ("date", "page"),
    "page_query": ("date", "page", "query"),
}


@dataclass(frozen=True)
class Fact:
    """1 日 1 粒度の 1 行。`keys` は粒度ごとの次元の値（日付を除く）。"""

    day: date
    keys: tuple[str, ...]
    clicks: int
    impressions: int
    ctr: float
    position: float

    def to_dict(self, grain: str) -> dict[str, Any]:
        names = GRAINS[grain][1:]
        out: dict[str, Any] = {"date": self.day.isoformat()}
        out.update(dict(zip(names, self.keys, strict=False)))
        out["clicks"] = self.clicks
        out["impressions"] = self.impressions
        out["ctr"] = round(self.ctr, 6)
        out["position"] = round(self.position, 2)
        return out

    @classmethod
    def from_dict(cls, row: dict[str, Any], grain: str) -> Fact:
        names = GRAINS[grain][1:]
        return cls(
            day=date.fromisoformat(row["date"]),
            keys=tuple(str(row.get(n, "")) for n in names),
            clicks=int(row.get("clicks", 0)),
            impressions=int(row.get("impressions", 0)),
            ctr=float(row.get("ctr", 0.0)),
            position=float(row.get("position", 0.0)),
        )


class SearchStore:
    """`data/search/` の読み書き。"""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "search"

    def _path(self, grain: str, day: date) -> Path:
        return self.root / "performance" / grain / f"{day:%Y-%m}.jsonl"

    def months(self, grain: str) -> list[Path]:
        directory = self.root / "performance" / grain
        return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []

    def read(self, grain: str, *, since: date | None = None) -> list[Fact]:
        out: list[Fact] = []
        for path in self.months(grain):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                fact = Fact.from_dict(json.loads(line), grain)
                if since is None or fact.day >= since:
                    out.append(fact)
        return out

    def write(self, grain: str, facts: Iterable[Fact], *, replace_days: set[date]) -> list[Path]:
        """`replace_days` の行を新しいものに置き換える。他の日はそのまま残す。"""
        fresh: dict[str, list[Fact]] = {}
        for fact in facts:
            fresh.setdefault(f"{fact.day:%Y-%m}", []).append(fact)
        touched = {f"{d:%Y-%m}" for d in replace_days} | set(fresh)
        written: list[Path] = []
        for month in sorted(touched):
            path = self.root / "performance" / grain / f"{month}.jsonl"
            kept: list[Fact] = []
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    fact = Fact.from_dict(json.loads(line), grain)
                    if fact.day not in replace_days:
                        kept.append(fact)
            rows = sorted(kept + fresh.get(month, []), key=lambda f: (f.day, f.keys))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "".join(json.dumps(f.to_dict(grain), ensure_ascii=False) + "\n" for f in rows),
                encoding="utf-8",
                newline="\n",
            )
            written.append(path)
        return written

    # --- インデックスの状態 -------------------------------------------------

    @property
    def urls_path(self) -> Path:
        return self.root / "index" / "urls.json"

    def read_urls(self) -> dict[str, dict[str, Any]]:
        if not self.urls_path.is_file():
            return {}
        data = json.loads(self.urls_path.read_text(encoding="utf-8"))
        return dict(data.get("urls", {}))

    def write_urls(self, urls: dict[str, dict[str, Any]], *, checked_at: datetime) -> Path:
        self.urls_path.parent.mkdir(parents=True, exist_ok=True)
        self.urls_path.write_text(
            json.dumps(
                {"checked_at": checked_at.isoformat(), "urls": dict(sorted(urls.items()))},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        return self.urls_path

    @property
    def sitemaps_path(self) -> Path:
        return self.root / "index" / "sitemaps.json"

    def write_sitemaps(self, rows: list[dict[str, Any]], *, checked_at: datetime) -> Path:
        self.sitemaps_path.parent.mkdir(parents=True, exist_ok=True)
        self.sitemaps_path.write_text(
            json.dumps(
                {"checked_at": checked_at.isoformat(), "sitemaps": rows},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        return self.sitemaps_path

    @property
    def states_path(self) -> Path:
        return self.root / "index" / "states.json"

    def read_state_history(self) -> dict[str, dict[str, int]]:
        """日付 → 状態ごとの件数。"""
        if not self.states_path.is_file():
            return {}
        data = json.loads(self.states_path.read_text(encoding="utf-8"))
        return {day: dict(counts) for day, counts in data.get("days", {}).items()}

    def write_state_counts(self, day: date, counts: dict[str, int]) -> Path:
        """その日の件数を置き換える（同じ日に何度取り込んでも 1 行）。"""
        history = self.read_state_history()
        history[day.isoformat()] = dict(sorted(counts.items()))
        self.states_path.parent.mkdir(parents=True, exist_ok=True)
        self.states_path.write_text(
            json.dumps({"days": dict(sorted(history.items()))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return self.states_path

    @property
    def variants_path(self) -> Path:
        return self.root / "index" / "variants.json"

    def read_variants(self) -> dict[str, dict[str, Any]]:
        if not self.variants_path.is_file():
            return {}
        data = json.loads(self.variants_path.read_text(encoding="utf-8"))
        return dict(data.get("urls", {}))

    def write_variants(self, urls: dict[str, dict[str, Any]], *, checked_at: datetime) -> Path:
        self.variants_path.parent.mkdir(parents=True, exist_ok=True)
        self.variants_path.write_text(
            json.dumps(
                {"checked_at": checked_at.isoformat(), "urls": dict(sorted(urls.items()))},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        return self.variants_path

    def read_sitemaps(self) -> dict[str, Any]:
        if not self.sitemaps_path.is_file():
            return {}
        return json.loads(self.sitemaps_path.read_text(encoding="utf-8"))
