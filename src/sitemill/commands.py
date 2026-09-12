"""CLI の各コマンドの実体。typer から呼ぶが、テストからも直接呼べる。"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.build.site import SiteBuilder
from sitemill.deploy import DeployPlan, deploy
from sitemill.diff.schedule import IntervalPolicy, source_due
from sitemill.diff.state import CrawlState
from sitemill.extract.llm import LLMError, LLMProvider, make_provider
from sitemill.extract.pipeline import extract_page, prepare_input
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.crawler import crawl_source
from sitemill.fetch.discover import discover_source
from sitemill.metrics.evalcases import EvalResult, load_eval_cases, record_responses, run_eval
from sitemill.metrics.extraction import ExtractionMetrics
from sitemill.metrics.reports import new_report, save_report
from sitemill.models import ExtractorInfo, Provenance, RunReport, Source, utcnow
from sitemill.service import Service, check_crawl_gate, load_service
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
        # 巡回してよい運営主体かをここで必ず通す（ADR 0017）。ids で絞る前に全件を見る
        check_crawl_gate(self.service, sources)
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
    workers: int | None = None,
) -> RunReport:
    """robots と間隔を守って巡回する。source（≒ホスト）単位で並列に取得する（ADR 0013）。

    1 ホストあたりの間隔は PoliteClient のホスト別ロックが守るので、並列でも縮まらない。
    巡回状態は全 source が終わってからまとめて保存する（並列中に直列化しない）。
    """
    report = new_report(rt.service.id, "crawl")
    state = CrawlState.load(rt.state_path)
    raw = RawCache(rt.ws.raw_dir)
    limit = max_pages or rt.ws.site.crawl.max_pages_per_source
    workers = max(1, workers or rt.ws.site.crawl.max_workers)
    sources = rt.sources(source_ids)
    crawlable = [s for s in sources if s.crawlable]
    for _ in range(len(sources) - len(crawlable)):
        report.bump("crawl", "skipped_link_only")
    cfg = rt.ws.site.crawl
    daily_kinds = frozenset(cfg.always_daily_kinds)
    # 取りに行く対象。(source, 種別で絞るか) の組。None なら source 全体
    targets: list[tuple[Source, frozenset[str] | None]] = [(s, None) for s in crawlable]
    if cfg.adaptive_interval and not force:
        # 動きの無いサイトは間隔を延ばす（下限は週 1 回）。相手サイトへの負荷を下げる。
        # ただし告知ページ（always_daily_kinds）は毎日取りに行く。臨時休業と運休は
        # 「変化の少ないページに突然出る」ので、間隔を延ばすと最も重要な情報を取り逃がす
        policy = IntervalPolicy(
            fresh_days=cfg.fresh_days,
            slow_after_days=cfg.slow_after_days,
            mid_interval_days=cfg.mid_interval_days,
            max_interval_days=cfg.max_interval_days,
        )
        now = utcnow()
        targets = []
        for src in crawlable:
            ok, interval, why = source_due(src.id, state, now=now, policy=policy)
            del interval
            # まだ 1 度も取っていない seed があるなら、間隔に関わらず取りに行く。
            # seed を足した直後に「まだ期日でない」で見送ると、新しいページが最長 7 日間
            # 取得されず、その間ずっと情報が欠けたままになる
            unseen = [p.url for p in src.pages if state.get(p.url) is None]
            if ok or unseen:
                if unseen and not ok:
                    report.bump("crawl", "new_seeds", len(unseen))
                    log.info("%s: 未取得の seed が %d 件あるので取りに行く", src.id, len(unseen))
                targets.append((src, None))
                continue
            if daily_kinds and any(str(p.kind) in daily_kinds for p in src.pages):
                targets.append((src, daily_kinds))
                report.bump("crawl", "daily_kinds_only")
                log.debug("%s: 告知ページだけ取りに行く（%s）", src.id, why)
                continue
            report.bump("crawl", "skipped_not_due")
            log.debug("%s: 今回は取りに行かない（%s）", src.id, why)
    with rt.client() as client:

        def one(target: tuple[Source, frozenset[str] | None]) -> Any:
            src, only_kinds = target
            return crawl_source(
                src, client, state, raw, max_pages=limit, force=force, only_kinds=only_kinds
            )

        try:
            if workers > 1 and len(targets) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    summaries = list(pool.map(one, targets))
            else:
                summaries = [one(target) for target in targets]
        finally:
            state.save(rt.state_path)
        for summary in summaries:
            for key in ("fetched", "changed", "unchanged", "not_modified", "errors"):
                report.bump("crawl", key, summary.count(key))
            for page in summary.pages:
                if page.error and not page.not_modified:
                    report.errors.append(f"{page.url}: {page.error}")
        report.bump("crawl", "requests", client.request_count)
        report.bump("crawl", "workers", workers)
    save_report(rt.ws.runs_dir, report)
    return report


class _Budget:
    """並列 extract で「処理するページ数の上限」を全体で共有するカウンタ。"""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.limit is not None and self.used >= self.limit:
                return False
            self.used += 1
            return True


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
    workers: int | None = None,
) -> RunReport:
    """変化したページ（pending_extract）だけを LLM に渡し、サービスにレコードを取り込ませる。

    source 単位で並列化する（1 source 内は順次）。レコード店は source ごとのファイルなので
    競合しない。集計（report / metrics）は主スレッドで行う。
    """
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
    workers = max(1, workers or ws.site.crawl.max_workers)
    sources = [s for s in rt.sources(source_ids) if s.crawlable]
    budget = _Budget(limit)
    # 情報源の pages が巡回対象のすべてか。宣言したサービスだけ、seed から外れた状態を捨てる。
    # 発見でページを足すサービス（akiya-atlas は一覧から詳細ページを見つける）では捨ててはいけない
    declared_pages_only = bool(getattr(rt.service, "declared_pages_only", False))

    def run_source(src: Source) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seeded = {p.url for p in src.pages}
        stale = (
            [st.url for st in state.for_source(src.id) if st.url not in seeded]
            if declared_pages_only
            else []
        )
        if stale:
            # seed から外した URL。状態が残っているかぎり抽出され続け、外した判断が効かない
            # （別の施設のページを外しても、その施設の事実が入り続ける）
            state.forget(stale)
            out.append({"bump": ("extract", "unseeded_dropped", len(stale))})
            log.info("%s: seed に無い %d 件の状態を捨てる", src.id, len(stale))
        for st in state.for_source(src.id):
            if st.error is not None or not (st.pending_extract or all_pages):
                continue
            if not budget.take():
                break
            spec = rt.service.extraction_spec(st.kind)
            if spec is None:
                st.pending_extract = False
                out.append({"bump": ("extract", "no_spec")})
                continue
            html = raw.load_text(src.id, st.url)
            if html is None:
                out.append(
                    {
                        "error": f"{st.url}: 生 HTML のキャッシュがない。先に crawl を実行する",
                        "bump": ("extract", "missing_cache"),
                    }
                )
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
                out.append({"error": f"{st.url}: {e}", "bump": ("extract", "llm_errors")})
                continue
            provenance = _provenance(st, src, spec.prompt_version, ext, now)
            counts = rt.service.ingest(
                ws, source=src, url=st.url, kind=st.kind, items=ext.items, provenance=provenance
            )
            st.pending_extract = False
            st.extracted_hash = st.content_hash
            st.extracted_at = now
            st.prompt_version = spec.prompt_version
            out.append({"ext": ext, "counts": counts})
        return out

    try:
        if workers > 1 and len(sources) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(run_source, sources))
        else:
            results = [run_source(src) for src in sources]
    finally:
        state.save(rt.state_path)

    for rows in results:
        for row in rows:
            if "error" in row:
                report.errors.append(row["error"])
            if "bump" in row:
                report.bump(*row["bump"])
            ext = row.get("ext")
            if ext is None:
                continue
            report.bump("extract", "pages")
            report.bump("extract", "items", len(ext.items))
            for key, n in row["counts"].items():
                report.bump("ingest", key, n)
            report.llm.add(
                input_tokens=ext.llm.input_tokens or 0,
                output_tokens=ext.llm.output_tokens or 0,
                cached=ext.llm.cached,
            )
            metrics.merge(ext.metrics)

    rt.service.finalize(ws, now=now)
    report.extraction_metrics = metrics.model_dump(mode="json")
    save_report(ws.runs_dir, report)
    return report


def cmd_finalize(rt: Runtime) -> RunReport:
    """抽出後の後処理だけを実行する（サービスの finalize: stale 判定や所在地の正規化など）。"""
    report = new_report(rt.service.id, "finalize")
    rt.service.finalize(rt.ws, now=utcnow())
    save_report(rt.ws.runs_dir, report)
    return report


def cmd_build(rt: Runtime) -> RunReport:
    report = new_report(rt.service.id, "build")
    result = SiteBuilder(rt.ws, rt.service).build()
    report.bump("build", "pages", result.pages)
    report.bump("build", "files", len(result.files))
    report.notes.extend(result.warnings)
    save_report(rt.ws.runs_dir, report)
    return report


def cmd_heal(rt: Runtime, source_ids: list[str] | None = None) -> RunReport:
    """巡回したのに成果が 0 件の source を自己修復する（サービスの `heal` フック）。

    サービスが `heal(ws, *, client, source_ids) -> dict` を実装していれば呼ぶ。返り値は
    {"changed": [説明...], "recrawl": [source id...], "downgraded": [source id...]} を期待し、
    差し替えた source はその場で crawl→extract し直す（build はこの後の工程で行う）。
    """
    report = new_report(rt.service.id, "heal")
    hook = getattr(rt.service, "heal", None)
    if hook is None:
        report.notes.append("サービスに heal フックが無いため何もしない")
        save_report(rt.ws.runs_dir, report)
        return report
    with rt.client() as client:
        result: dict[str, Any] = hook(rt.ws, client=client, source_ids=source_ids) or {}
        report.bump("heal", "requests", client.request_count)
    for line in result.get("changed", []):
        report.notes.append(str(line))
    recrawl = [str(s) for s in result.get("recrawl", [])]
    report.bump("heal", "checked", int(result.get("checked", 0)))
    report.bump("heal", "swapped", len(recrawl))
    report.bump("heal", "downgraded", len(result.get("downgraded", [])))
    if recrawl:
        crawl = cmd_crawl(rt, recrawl, force=True)
        extract = cmd_extract(rt, recrawl)
        for key in ("fetched", "errors"):
            report.bump("recrawl", key, crawl.stages.get("crawl", {}).get(key, 0))
        for key in ("pages", "items"):
            report.bump("recrawl", key, extract.stages.get("extract", {}).get(key, 0))
        report.errors.extend(crawl.errors + extract.errors)
    save_report(rt.ws.runs_dir, report)
    return report


def cmd_run(
    rt: Runtime, source_ids: list[str] | None = None, *, workers: int | None = None
) -> list[RunReport]:
    """crawl → extract → heal → build。discover は候補の確認が要るため含めない。"""
    return [
        cmd_crawl(rt, source_ids, workers=workers),
        cmd_extract(rt, source_ids, workers=workers),
        cmd_heal(rt, source_ids),
        cmd_build(rt),
    ]


def cmd_eval(
    rt: Runtime,
    *,
    model: str = "fixture",
    record: bool = False,
    provider: LLMProvider | None = None,
) -> EvalResult:
    """保存済み fixture で抽出精度を計測する。record=True なら本番の応答を取り直してから計測。"""
    eval_dir = rt.service.eval_dir(rt.ws) or (rt.ws.fixtures_dir / "eval")
    # record のときは、まだ応答の無い fixture も読む（読まないと永遠に記録されない）
    cases = load_eval_cases(eval_dir, require_response=not record)
    llm_cfg = rt.ws.site.llm
    recorded: list[dict[str, Any]] = []
    if record:
        if provider is None:
            name = rt.ws.secrets.sitemill_llm_provider or llm_cfg.provider
            provider = make_provider(name, secrets=rt.ws.secrets, cache_dir=rt.ws.llm_cache_dir)
        recorded = record_responses(
            cases,
            rt.service.extraction_spec,
            provider,
            model=llm_cfg.model,
            root=eval_dir,
            max_tokens=llm_cfg.max_output_tokens,
            temperature=llm_cfg.temperature,
            max_chars=llm_cfg.max_input_chars,
        )
        model = llm_cfg.model
        cases = load_eval_cases(eval_dir)  # 応答を書いたので読み直す
    result = run_eval(
        cases, rt.service.extraction_spec, model=model, max_chars=llm_cfg.max_input_chars
    )
    result.recorded = recorded
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
    sources = rt.sources()
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
