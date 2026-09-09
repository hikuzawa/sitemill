"""JSON / JSONL の読み書き。key をソートして差分が読める形で書く（ADR 0002）。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _default(o: Any) -> Any:
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, set | frozenset):
        return sorted(o)
    raise TypeError(f"JSON にできない型: {type(o).__name__}")


def dumps(obj: Any, *, indent: int | None = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=indent, default=_default)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(obj) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(dumps(row, indent=None) + "\n")
            n += 1
    tmp.replace(path)
    return n


def read_jsonl(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    out: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out
