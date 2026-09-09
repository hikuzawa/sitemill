"""サービスが実装するプロトコル（ADR 0006）。sitemill はこの面だけを通してサービスを扱う。"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from sitemill.extract.spec import ExtractedItem, ExtractionSpec
from sitemill.models import Page, Provenance, Redirect, Source
from sitemill.settings import Workspace


@runtime_checkable
class Service(Protocol):
    """サービス側が提供するもの。データ・テンプレート・スキーマはサービスのリポジトリにある。"""

    id: str

    def sources(self, ws: Workspace) -> list[Source]: ...

    def extraction_spec(self, kind: str) -> ExtractionSpec | None: ...

    def ingest(
        self,
        ws: Workspace,
        *,
        source: Source,
        url: str,
        kind: str,
        items: Sequence[ExtractedItem],
        provenance: Provenance,
    ) -> dict[str, int]: ...

    def finalize(self, ws: Workspace, *, now: datetime) -> None: ...

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]: ...

    def search_index(self, ws: Workspace) -> Any: ...

    def redirects(self, ws: Workspace) -> list[Redirect]: ...

    def eval_dir(self, ws: Workspace) -> Path | None: ...


def load_service(spec: str) -> Service:
    """ "pkg.module:attr" 形式の指定からサービスオブジェクトを読み込む。"""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError(f"service は 'pkg.module:attr' の形で指定する: {spec!r}")
    module = importlib.import_module(module_name)
    service = getattr(module, attr)
    if not isinstance(service, Service):
        raise TypeError(f"{spec} は Service プロトコルを満たしていない")
    return service


def load_sources_yaml(path: Path, *, key: str = "sources") -> list[Source]:
    """YAML の {key: [...]} を Source のリストにする。余分なキーは無視される。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get(key, []) if isinstance(data, dict) else []
    sources = [Source.model_validate(e) for e in entries]
    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: Source の id が重複している")
    return sources
