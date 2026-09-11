"""fixture による抽出精度の計測（ADR 0010）。外部アクセスなしで抽出パイプラインを通す。

ケースのディレクトリ構成（tests/fixtures/eval/<case>/）:
- page.html          保存済みの HTML（meta.json の html で別パスも指定できる）
- meta.json          {"url": ..., "kind": "listing_index",
                      "key_field": "listing_no", "selector": null}
- llm_response.json  保存済みの LLM 応答（本番と同じ JSON）
- expected.json      {"records": [{"listing_no": "329", "price": 9800000, ...}]}
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sitemill.extract.llm.base import LLMProvider
from sitemill.extract.llm.fixture import FixtureProvider
from sitemill.extract.pipeline import extract_page, prepare_input
from sitemill.extract.spec import ExtractionSpec
from sitemill.metrics.extraction import ExtractionMetrics
from sitemill.store.jsonio import write_json


@dataclass
class EvalCase:
    name: str
    url: str
    kind: str
    html: str
    llm_response: dict[str, Any]
    expected: list[dict[str, Any]]
    key_field: str = "listing_no"
    selector: str | None = None


def load_eval_cases(root: Path, *, require_response: bool = True) -> list[EvalCase]:
    """fixture を読む。

    require_response=False のときは、まだ応答が無いケースも読む（`eval --record` で
    これから応答を取るため）。応答が要るのは計測のときだけ。
    """
    cases: list[EvalCase] = []
    if not root.is_dir():
        return cases
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        names = ("meta.json", "llm_response.json") if require_response else ("meta.json",)
        if not all((case_dir / n).is_file() for n in names):
            continue
        meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
        # html は既定で page.html。共有 fixture を使うときは meta.json の "html" に相対パスを書く
        html_path = (case_dir / meta.get("html", "page.html")).resolve()
        if not html_path.is_file():
            continue
        expected_path = case_dir / "expected.json"
        expected: list[dict[str, Any]] = []
        if expected_path.is_file():
            expected = json.loads(expected_path.read_text(encoding="utf-8")).get("records", [])
        cases.append(
            EvalCase(
                name=case_dir.name,
                url=meta["url"],
                kind=meta.get("kind", "listing_index"),
                html=html_path.read_text(encoding="utf-8"),
                llm_response=(
                    json.loads((case_dir / "llm_response.json").read_text(encoding="utf-8"))
                    if (case_dir / "llm_response.json").is_file()
                    else {}
                ),
                expected=expected,
                key_field=meta.get("key_field", "listing_no"),
                selector=meta.get("selector"),
            )
        )
    return cases


@dataclass
class FieldAccuracy:
    correct: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalResult:
    cases: int = 0
    metrics: ExtractionMetrics = field(default_factory=ExtractionMetrics)
    accuracy: dict[str, FieldAccuracy] = field(default_factory=dict)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    unmatched_expected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    recorded: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "metrics": self.metrics.model_dump(mode="json"),
            "accuracy": {
                k: {"correct": v.correct, "total": v.total, "rate": round(v.rate, 3)}
                for k, v in sorted(self.accuracy.items())
            },
            "mismatches": self.mismatches,
            "unmatched_expected": self.unmatched_expected,
            "skipped": self.skipped,
            "recorded": self.recorded,
        }

    def table(self) -> str:
        lines = [f"cases={self.cases} skipped={len(self.skipped)}", self.metrics.table(), ""]
        lines.append(f"{'field':<16}{'accuracy':>9}{'correct':>9}{'total':>7}")
        for name, acc in sorted(self.accuracy.items()):
            lines.append(f"{name:<16}{acc.rate:>9.2f}{acc.correct:>9}{acc.total:>7}")
        if self.unmatched_expected:
            lines.append(f"unmatched expected keys: {', '.join(self.unmatched_expected)}")
        for m in self.mismatches[:20]:
            lines.append(
                f"  {m['case']} {m['key']}.{m['field']}: "
                f"expected={m['expected']!r} got={m['got']!r}"
            )
        return "\n".join(lines)


def run_eval(
    cases: list[EvalCase],
    spec_for_kind: Callable[[str], ExtractionSpec | None],
    *,
    model: str = "fixture",
    max_chars: int = 60_000,
) -> EvalResult:
    result = EvalResult()
    for case in cases:
        spec = spec_for_kind(case.kind)
        if spec is None:
            result.skipped.append(f"{case.name}: kind={case.kind} に抽出仕様がない")
            continue
        provider = FixtureProvider([case.llm_response])
        page = prepare_input(
            case.html, url=case.url, kind=case.kind, selector=case.selector, max_chars=max_chars
        )
        extraction = extract_page(spec, page, provider, model=model)
        result.cases += 1
        result.metrics.merge(extraction.metrics)
        by_key: dict[str, Any] = {}
        for item in extraction.items:
            key = item.value(case.key_field)
            if key is None:
                key = item.free.get(case.key_field) or item.raw.get(case.key_field)
            if key is not None:
                by_key.setdefault(str(key), item)
        for exp in case.expected:
            key = str(exp.get(case.key_field))
            item = by_key.get(key)
            if item is None:
                result.unmatched_expected.append(f"{case.name}:{key}")
                continue
            for fname, expected_value in exp.items():
                if fname == case.key_field:
                    continue
                got = item.value(fname) if fname in item.fields else item.free.get(fname)
                acc = result.accuracy.setdefault(fname, FieldAccuracy())
                acc.total += 1
                if got == expected_value:
                    acc.correct += 1
                else:
                    result.mismatches.append(
                        {
                            "case": case.name,
                            "key": key,
                            "field": fname,
                            "expected": expected_value,
                            "got": got,
                        }
                    )
    return result


def record_responses(
    cases: list[EvalCase],
    spec_for_kind: Callable[[str], ExtractionSpec | None],
    provider: LLMProvider,
    *,
    model: str,
    root: Path,
    max_tokens: int = 16_000,
    temperature: float | None = 0.0,
    max_chars: int = 60_000,
) -> list[dict[str, Any]]:
    """本番プロバイダで応答を取り直し llm_response.json に保存する（前の応答は .previous.json）。"""
    written: list[dict[str, Any]] = []
    for case in cases:
        spec = spec_for_kind(case.kind)
        if spec is None:
            continue
        page = prepare_input(
            case.html, url=case.url, kind=case.kind, selector=case.selector, max_chars=max_chars
        )
        result = provider.complete_json(
            system=spec.system_prompt,
            user=spec.user_prompt(url=page.url, kind=page.kind, text=page.text),
            schema=spec.output_schema,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        case_dir = root / case.name
        target = case_dir / "llm_response.json"
        if target.is_file():
            target.replace(case_dir / "llm_response.previous.json")
        write_json(target, result.data)
        case.llm_response = result.data
        written.append(
            {
                "case": case.name,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cached": result.cached,
            }
        )
    return written
