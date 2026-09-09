"""CLI の各コマンドの実体。typer から呼ぶが、テストからも直接呼べる。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.build.site import SiteBuilder
from sitemill.deploy import DeployPlan, deploy
from sitemill.diff.state import CrawlState
from sitemill.extract.llm import LLMError, LLMProvider, make_provider
from sitemill.extract.pipeline import extract_page, prepare_input
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.crawler import crawl_source
from sitemill.fetch.discover import discover_source
from sitemill.metrics.evalcases import EvalResult, load_eval_cases, run_eval
from sitemill.metrics.extraction import ExtractionMetrics
from sitemill.metrics.reports import new_report, save_report
from sitemill.models import ExtractorInfo, Provenance, RunReport, Source, utcnow
from sitemill.service import Service, load_service
from sitemill.settings import Workspace
from sitemill.store.jsonio import read_jsonl, write_json
from sitemill.store.raw import RawCache

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    ws: Workspace
    service: Service

    @classmethod
    def open(cls, root: Path | None = None, *, service: Service | None = None) -> Runtime:
        ws = Workspace.open(root)
        ws.ensure_dirs()
        return cls(ws=ws, service=service or load_service(ws.site.service))

    @property
    def state_path(self) -> Path:
        return self.ws.state_dir / "crawl.json"

    def client(self) -> PoliteClient:
        crawl = self.ws.site.crawl
        return PoliteClient(
            self.ws.site.user_agent,
            default_delay=crawl.default_delay_seconds,
            jitter=crawl.jitter_seconds,
            timeout=crawl.timeout_seconds,
        )

    def sources(self, ids: list[str] | None = None) -> list[Source]:
        sources = self.service.sources(self.ws)
        if ids:
            wanted = set(ids)
            missing = wanted - {s.id for s in sources}
            if missing:
                raise ValueError(f"未知の source id: {', '.join(sorted(missing))}")
            sources = [s for s in sources if s.id in wanted]
        return sources


def cmd_discover(rt: Runtime, source_ids: list[str] | None = None) -> RunReport:
    report = new_report(rt.service.id, "discover")
    out_dir = rt.ws.state_dir / "discovery"
    with rt.client() as client:
        for src in rt.sources(source_ids):
            if not src.crawlable:
                report.bump("discover", "skipped_link_only")
                continue
            candidates = discover_source(src, client)
            write_json(
                out_dir / f"{src.id}.json",
                {
                    "source_id": src.id,
                    "checked_at": report.started_at.isoformat(),
                    "candidates": [c.__dict__ for c in candidates],
                },
            )
            report.bump("discover", "sources")
            report.bump("discover", "candidates", len(candidates))
            log.info("%s: %d 候補", src.id, len(candidates))
    save_report(rt.ws.runs_dir, report)
    return report


def cmd_crawl(
    rt: Runtime,
    source_ids: list[str] | None = None,
    *,
    force: bool = False,
    max_pages: int | None = None,
) -> RunReport:
    report = new_report(rt.service.id, "crawl")
    state = CrawlState.load(rt.state_path)
    raw = RawCache(rt.ws.raw_dir)
    limit = max_pages or rt.ws.site.crawl.max_pages_per_source
    with rt.client() as client:
        for src in rt.sources(source_ids):
            if not src.crawlable:
                report.bump("crawl", "skipped_link_only")
                continue
            summary = crawl_source(src, client, state, raw, max_pages=limit, force=force)
            for key in ("fetched", "changed", "unchanged", "not_modified", "errors"):
                report.bump("crawl", key, summary.count(key))
            for page in summary.pages:
                if page.error and not page.not_modified:
                    report.errors.append(f"{page.url}: {page.error}")
            state.save(rt.state_path)
        report.bump("crawl", "requests", client.request_count)
    save_report(rt.ws.runs_dir, report)
    return report


def _provenance(st: Any, src: Source, spec_version: str, ext: Any, now: datetime) -> Provenance:
    return Provenance(
        source_url=st.url,
        fetched_at=st.fetched_at or now,
        content_hash=st.content_hash or "",
        page_kind=st.kind,
        extractor=ExtractorInfo(
            provider=ext.llm.provider,
            model=ext.llm.model,
            prompt_version=spec_version,
            extracted_at=now,
            input_tokens=ext.llm.input_tokens,
            output_tokens=ext.llm.output_tokens,
            cached=ext.llm.cached,
        ),
        license=src.license,
    )


def cmd_extract(
    rt: Runtime,
    source_ids: list[str] | None = None,
    *,
    all_pages: bool = False,
    limit: int | None = None,
    provider: LLMProvider | None = None,
) -> RunReport:
    """変化したページ（pending_extract）だけを LLM に渡し、サービスにレコードを取り込ませる。"""
    report = new_report(rt.service.id, "extract")
    ws = rt.ws
    llm_cfg = ws.site.llm
    if provider is None:
        name = ws.secrets.sitemill_llm_provider or llm_cfg.provider
        provider = make_provider(name, secrets=ws.secrets, cache_dir=ws.llm_cache_dir)
    state = CrawlState.load(rt.state_path)
    raw = RawCache(ws.raw_dir)
    metrics = ExtractionMetrics()
    now = utcnow()
    processed = 0

    for src in rt.sources(source_ids):
        if not src.crawlable:
            continue
        for st in state.for_source(src.id):
            if limit is not None and processed >= limit:
                break
            if st.error is not None or not (st.pending_extract or all_pages):
                continue
            spec = rt.service.extraction_spec(st.kind)
            if spec is None:
                st.pending_extract = False
                report.bump("extract", "no_spec")
                continue
            html = raw.load_text(src.id, st.url)
            if html is None:
                report.errors.append(f"{st.url}: 生 HTML のキャッシュがない。先に crawl を実行する")
                report.bump("extract", "missing_cache")
                continue
            page = prepare_input(
                html,
                url=st.url,
                kind=st.kind,
                selector=src.content_selector,
                max_chars=llm_cfg.max_input_chars,
            )
            try:
                ext = extract_page(
                    spec,
                    page,
                    provider,
                    model=llm_cfg.model,
                    max_tokens=llm_cfg.max_output_tokens,
                    temperature=llm_cfg.temperature,
                )
            except LLMError as e:
                report.errors.append(f"{st.url}: {e}")
                report.bump("extract", "llm_errors")
                continue
            processed += 1
            provenance = _provenance(st, src, spec.prompt_version, ext, now)
            counts = rt.service.ingest(
                ws, source=src, url=st.url, kind=st.kind, items=ext.items, provenance=provenance
            )
            report.bump("extract", "pages")
            report.bump("extract", "items", len(ext.items))
            for key, n in counts.items():
                report.bump("ingest", key, n)
            report.llm.add(
                input_tokens=ext.llm.input_tokens or 0,
                output_tokens=ext.llm.output_tokens or 0,
                cached=ext.llm.cached,
            )
            metrics.merge(ext.metrics)
            st.pending_extract = False
            st.extracted_hash = st.content_hash
            st.extracted_at = now
            st.prompt_version = spec.prompt_version
        state.save(rt.state_path)

    rt.service.finalize(ws, now=now)
    report.extraction_metrics = metrics.model_dump(mode="json")
    save_report(ws.runs_dir, report)
    return report


def cmd_build(rt: Runtime) -> RunReport:
    report = new_report(rt.service.id, "build")
    result = SiteBuilder(rt.ws, rt.service).build()
    report.bump("build", "pages", result.pages)
    report.bump("build", "files", len(result.files))
    report.notes.extend(result.warnings)
    save_report(rt.ws.runs_dir, report)
    return report


def cmd_run(rt: Runtime, source_ids: list[str] | None = None) -> list[RunReport]:
    """crawl → extract → build。discover は候補の確認が要るため含めない。"""
    return [cmd_crawl(rt, source_ids), cmd_extract(rt, source_ids), cmd_build(rt)]


def cmd_eval(rt: Runtime, *, model: str = "fixture") -> EvalResult:
    eval_dir = rt.service.eval_dir(rt.ws) or (rt.ws.fixtures_dir / "eval")
    cases = load_eval_cases(eval_dir)
    result = run_eval(
        cases, rt.service.extraction_spec, model=model, max_chars=rt.ws.site.llm.max_input_chars
    )
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    write_json(rt.ws.runs_dir / f"{stamp}-eval.json", result.to_dict())
    write_json(rt.ws.runs_dir / "latest-eval.json", result.to_dict())
    return result


def cmd_deploy(rt: Runtime, *, dry_run: bool = True, project: str | None = None) -> DeployPlan:
    ws = rt.ws
    return deploy(
        ws.dist_dir,
        project_name=project or ws.site.id,
        account_id=ws.secrets.cloudflare_account_id,
        api_token=ws.secrets.cloudflare_api_token,
        dry_run=dry_run,
    )


def cmd_status(rt: Runtime) -> dict[str, Any]:
    state = CrawlState.load(rt.state_path)
    sources = rt.service.sources(rt.ws)
    records = {}
    for src in sources:
        path = rt.ws.records_dir / f"{src.id}.jsonl"
        rows = read_jsonl(path)
        records[src.id] = {
            "records": len(rows),
            "active": sum(1 for r in rows if r.get("status") == "active"),
            "urls": len(state.for_source(src.id)),
            "pending": len(state.pending(src.id)),
            "policy": src.policy.value,
        }
    return {"sources": len(sources), "urls": len(state.urls), "by_source": records}
