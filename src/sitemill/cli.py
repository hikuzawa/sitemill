"""sitemill CLI。サービスのルート（site.toml のある場所）で実行する。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from sitemill import __version__, commands
from sitemill.build.site import BuildError
from sitemill.extract.llm import LLMError
from sitemill.settings import SecretsError

app = typer.Typer(
    help="公的・公式サイトの監視→差分検知→構造化→静的サイト生成→デプロイ→計測",
    no_args_is_help=True,
)

RootOpt = Annotated[Path | None, typer.Option("--root", "-r", help="site.toml のあるディレクトリ")]
SourceOpt = Annotated[list[str] | None, typer.Option("--source", "-s", help="対象 source id")]
WorkersOpt = Annotated[
    int | None,
    typer.Option("--workers", "-w", help="ホスト並列数（既定は site.toml の crawl.max_workers）"),
]


def _runtime(root: Path | None) -> commands.Runtime:
    try:
        return commands.Runtime.open(root)
    except (FileNotFoundError, ValueError, TypeError) as e:
        typer.echo(f"エラー: {e}", err=True)
        raise typer.Exit(code=2) from e


def _report(report: object) -> None:
    from sitemill.models import RunReport

    if isinstance(report, RunReport):
        for stage, counts in report.stages.items():
            typer.echo(f"[{stage}] " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if report.llm.calls:
            llm = report.llm
            typer.echo(
                f"[llm] calls={llm.calls} cached={llm.cached_calls} "
                f"in={llm.input_tokens} out={llm.output_tokens}"
            )
        for err in report.errors:
            typer.echo(f"  ! {err}", err=True)


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="詳細ログ")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """バージョンを表示する。"""
    typer.echo(f"sitemill {__version__}")


@app.command()
def discover(root: RootOpt = None, source: SourceOpt = None) -> None:
    """公式サイトから目的ページの候補を集め、data/state/discovery/ に書き出す。"""
    _report(commands.cmd_discover(_runtime(root), source))


@app.command()
def crawl(
    root: RootOpt = None,
    source: SourceOpt = None,
    force: Annotated[bool, typer.Option("--force", help="条件付き GET を使わず取り直す")] = False,
    max_pages: Annotated[
        int | None, typer.Option("--max-pages", help="Source あたりの上限")
    ] = None,
    workers: WorkersOpt = None,
) -> None:
    """robots と間隔を守って巡回し、変化したページに印を付ける。"""
    _report(
        commands.cmd_crawl(
            _runtime(root), source, force=force, max_pages=max_pages, workers=workers
        )
    )


@app.command()
def extract(
    root: RootOpt = None,
    source: SourceOpt = None,
    all_pages: Annotated[bool, typer.Option("--all", help="変化の有無に関わらず全ページ")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="処理するページ数の上限")] = None,
    workers: WorkersOpt = None,
) -> None:
    """変化したページを LLM で構造化し、レコードに取り込む。"""
    rt = _runtime(root)
    try:
        report = commands.cmd_extract(rt, source, all_pages=all_pages, limit=limit, workers=workers)
    except SecretsError as e:
        typer.echo(f"停止: {e}", err=True)
        raise typer.Exit(code=3) from e
    except LLMError as e:
        typer.echo(f"LLM エラー: {e}", err=True)
        raise typer.Exit(code=4) from e
    _report(report)
    if report.extraction_metrics:
        from sitemill.metrics.extraction import ExtractionMetrics

        typer.echo(ExtractionMetrics.model_validate(report.extraction_metrics).table())


@app.command()
def finalize(root: RootOpt = None) -> None:
    """抽出後の後処理だけを実行する（stale 判定、所在地の正規化などサービスの finalize）。"""
    _report(commands.cmd_finalize(_runtime(root)))


@app.command()
def heal(root: RootOpt = None, source: SourceOpt = None) -> None:
    """巡回しても成果が 0 件の source を再評価し、より良い候補に差し替えるか状態を見直す。"""
    rt = _runtime(root)
    try:
        report = commands.cmd_heal(rt, source)
    except SecretsError as e:
        typer.echo(f"停止: {e}", err=True)
        raise typer.Exit(code=3) from e
    _report(report)
    for note in report.notes:
        typer.echo(f"  - {note}")


@app.command()
def build(root: RootOpt = None) -> None:
    """静的サイトを dist/ に生成する。信頼シグナルが欠けたページがあれば失敗する。"""
    try:
        _report(commands.cmd_build(_runtime(root)))
    except BuildError as e:
        typer.echo(f"ビルド失敗: {e}", err=True)
        raise typer.Exit(code=5) from e


@app.command()
def run(root: RootOpt = None, source: SourceOpt = None, workers: WorkersOpt = None) -> None:
    """crawl → extract → heal → build をまとめて実行する。"""
    rt = _runtime(root)
    try:
        for report in commands.cmd_run(rt, source, workers=workers):
            typer.echo(f"== {report.command} ==")
            _report(report)
    except SecretsError as e:
        typer.echo(f"停止: {e}", err=True)
        raise typer.Exit(code=3) from e
    except BuildError as e:
        typer.echo(f"ビルド失敗: {e}", err=True)
        raise typer.Exit(code=5) from e


@app.command(name="eval")
def eval_cmd(
    root: RootOpt = None,
    record: Annotated[
        bool, typer.Option("--record", help="本番の LLM で応答を取り直して fixture に保存する")
    ] = False,
) -> None:
    """保存済み fixture で抽出精度を計測する（--record 以外は外部アクセスなし）。"""
    try:
        result = commands.cmd_eval(_runtime(root), record=record)
    except SecretsError as e:
        typer.echo(f"停止: {e}", err=True)
        raise typer.Exit(code=3) from e
    for r in result.recorded:
        typer.echo(
            f"recorded {r['case']}: model={r['model']} "
            f"in={r['input_tokens']} out={r['output_tokens']}"
        )
    typer.echo(result.table())


@app.command()
def deploy(
    root: RootOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="検査のみ")] = True,
    project: Annotated[str | None, typer.Option("--project", help="Pages のプロジェクト名")] = None,
) -> None:
    """dist/ を検査し、--no-dry-run なら Cloudflare Pages に配置する。"""
    plan = commands.cmd_deploy(_runtime(root), dry_run=dry_run, project=project)
    for note in plan.notes:
        typer.echo(note)
    for problem in plan.problems:
        typer.echo(f"  ! {problem}", err=True)
    if not plan.ok:
        raise typer.Exit(code=6)


@app.command()
def status(root: RootOpt = None) -> None:
    """巡回状態とレコード数を表示する。"""
    info = commands.cmd_status(_runtime(root))
    typer.echo(f"sources={info['sources']} urls={info['urls']}")
    for sid, row in info["by_source"].items():
        typer.echo(
            f"  {sid:<20} policy={row['policy']:<9} urls={row['urls']:>3} "
            f"pending={row['pending']:>3} records={row['records']:>4} active={row['active']:>4}"
        )


@app.command(name="scan-secrets")
def scan_secrets(
    paths: Annotated[
        list[Path] | None, typer.Argument(help="走査するファイルやディレクトリ")
    ] = None,
    staged: Annotated[
        bool, typer.Option("--staged", help="git のステージ済み差分を走査（コミット前フック用）")
    ] = False,
    history: Annotated[
        bool, typer.Option("--history", help="全ブランチ・全履歴の追加行を走査")
    ] = False,
    repo: Annotated[Path, typer.Option("--repo", help="git リポジトリの場所")] = Path("."),
) -> None:
    """秘密らしき文字列と .env の混入を検出する。見つかれば終了コード 1。値は表示しない。"""
    from sitemill import secrets_scan

    findings: list[secrets_scan.Finding] = []
    if staged:
        findings += secrets_scan.scan_staged(repo)
    if history:
        findings += secrets_scan.scan_history(repo)
    if paths:
        findings += secrets_scan.scan_paths(paths)
    if not (staged or history or paths):
        findings += secrets_scan.scan_paths([repo])
    for f in findings:
        typer.echo(f"  ! {f}", err=True)
    if findings:
        typer.echo(f"秘密らしき文字列を {len(findings)} 件検出。コミットを中止する", err=True)
        raise typer.Exit(code=1)
    typer.echo("scan-secrets: 問題なし")


if __name__ == "__main__":  # pragma: no cover
    app()
