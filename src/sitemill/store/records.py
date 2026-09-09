"""レコードの永続化。record_id をキーに upsert し、変化を history に残す（ADR 0002）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sitemill.store.jsonio import read_jsonl, write_jsonl

META_KEYS = frozenset(
    {"record_id", "provenance", "first_seen_at", "last_seen_at", "history", "status"}
)
MergeFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class RecordStore:
    """1 ファイル（JSONL）に 1 Source 分のレコードを持つ。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict[str, Any]] = {}
        for row in read_jsonl(path):
            if isinstance(row, dict) and row.get("record_id"):
                self.records[row["record_id"]] = row

    def __len__(self) -> int:
        return len(self.records)

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.records.get(record_id)

    def all(self) -> list[dict[str, Any]]:
        return [self.records[k] for k in sorted(self.records)]

    def upsert(
        self,
        record_id: str,
        content: dict[str, Any],
        *,
        now: datetime,
        provenance: dict[str, Any] | None = None,
        merge: MergeFn | None = None,
    ) -> str:
        """created / updated / unchanged のいずれかを返す。"""
        now_s = now.isoformat()
        existing = self.records.get(record_id)
        if existing is None:
            record: dict[str, Any] = {"record_id": record_id, **content}
            record["provenance"] = provenance
            record["status"] = content.get("status", "active")
            record["first_seen_at"] = now_s
            record["last_seen_at"] = now_s
            record["history"] = [{"at": now_s, "event": "created"}]
            self.records[record_id] = record
            return "created"

        merged = merge(existing, content) if merge else content
        changed = sorted(
            k for k, v in merged.items() if k not in META_KEYS and existing.get(k) != v
        )
        for k, v in merged.items():
            if k not in META_KEYS:
                existing[k] = v
        if "status" in merged:
            existing["status"] = merged["status"]
        elif existing.get("status") == "stale":
            existing["status"] = "active"
            changed.append("status")
        existing["last_seen_at"] = now_s
        if provenance is not None:
            existing["provenance"] = provenance
        if changed:
            existing.setdefault("history", []).append(
                {"at": now_s, "event": "updated", "fields": changed}
            )
            return "updated"
        return "unchanged"

    def mark_stale(self, *, now: datetime, max_age_days: int = 30) -> int:
        """一定期間見つからないレコードを stale にする。削除はしない。"""
        cutoff = (now - timedelta(days=max_age_days)).isoformat()
        n = 0
        for record in self.records.values():
            if record.get("status") == "active" and record.get("last_seen_at", "") < cutoff:
                record["status"] = "stale"
                record.setdefault("history", []).append({"at": now.isoformat(), "event": "stale"})
                n += 1
        return n

    def save(self) -> int:
        return write_jsonl(self.path, self.all())
