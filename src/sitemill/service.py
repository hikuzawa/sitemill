"""サービスが実装するプロトコル（ADR 0006）。sitemill はこの面だけを通してサービスを扱う。"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from sitemill.extract.spec import ExtractedItem, ExtractionSpec
from sitemill.models import (
    DEFAULT_CRAWLABLE_OPERATORS,
    OFFICIAL_OPERATORS,
    OperatorKind,
    Page,
    Provenance,
    Redirect,
    Source,
)
from sitemill.settings import Workspace


@runtime_checkable
class Service(Protocol):
    """サービス側が提供するもの。データ・テンプレート・スキーマはサービスのリポジトリにある。

    任意の属性・フック（あれば使う）:
    - `crawlable_operator_kinds`: 巡回を許す運営主体の種別。宣言しなければ自治体だけ（ADR 0017）
    - `pii_policy(ws)`: 公開前の個人情報検査のポリシー
    - `heal(ws, *, client, source_ids)`: 成果 0 件の source を選び直す自己修復
    """

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


def crawlable_operator_kinds(service: object) -> frozenset[OperatorKind]:
    """そのサービスが巡回してよい運営主体の種別（ADR 0017）。

    サービスが `crawlable_operator_kinds` を宣言していればそれを使い、無ければ自治体だけに絞る。
    宣言できるのは公式と根拠づけできる種別（OFFICIAL_OPERATORS）の中だけ。
    """
    declared = getattr(service, "crawlable_operator_kinds", None)
    if declared is None:
        return DEFAULT_CRAWLABLE_OPERATORS
    try:
        kinds = frozenset(OperatorKind(k) for k in declared)
    except ValueError as e:
        raise ValueError(f"crawlable_operator_kinds に未知の運営主体の種別がある: {e}") from e
    outside = kinds - OFFICIAL_OPERATORS
    if outside:
        names = ", ".join(sorted(k.value for k in outside))
        raise ValueError(
            f"crawlable_operator_kinds に巡回できない種別が含まれている: {names}。"
            "民間のまとめサイト（third_party）と判定不能（unknown）は巡回しない"
        )
    return kinds


def check_crawl_gate(service: object, sources: Sequence[Source]) -> None:
    """policy=crawl の Source が、サービスの宣言した運営主体の種別に収まっているかを見る。

    Source 単体の検証（sitemill 側）は「公式と根拠づけできる種別かどうか」までしか見ない。
    サービスごとの線引き（空き家は自治体だけ、観光は施設公式まで）はここで担保する。
    """
    allowed = crawlable_operator_kinds(service)
    bad = [
        (s.id, s.operator_kind.value)
        for s in sources
        if s.crawlable and s.operator_kind not in allowed
    ]
    if bad:
        rows = ", ".join(f"{sid}（{kind}）" for sid, kind in bad[:5])
        names = ", ".join(sorted(k.value for k in allowed))
        raise ValueError(
            f"このサービスが巡回してよい運営主体は {names} だけ。"
            f"policy=crawl になっている対象外の source: {rows}"
        )


def load_sources_yaml(path: Path, *, key: str = "sources") -> list[Source]:
    """YAML の {key: [...]} を Source のリストにする。余分なキーは無視される。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get(key, []) if isinstance(data, dict) else []
    sources = [Source.model_validate(e) for e in entries]
    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: Source の id が重複している")
    return sources
