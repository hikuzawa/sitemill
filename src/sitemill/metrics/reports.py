"""実行レポートの作成と保存（ADR 0007）。"""

from __future__ import annotations

import os
from pathlib import Path

from sitemill.models import RunReport, utcnow
from sitemill.store.jsonio import read_json, write_json


def new_report(service_id: str, command: str) -> RunReport:
    # CI かどうかを残す。日次（Actions）と手元の作業を混ぜずに数えられるようにする
    return RunReport(
        service=service_id,
        command=command,
        started_at=utcnow(),
        ci=os.environ.get("CI", "").lower() == "true",
    )


def save_report(runs_dir: Path, report: RunReport) -> Path:
    if report.finished_at is None:
        report.finished_at = utcnow()
    stamp = report.started_at.strftime("%Y%m%d-%H%M%S")
    path = runs_dir / f"{stamp}-{report.command}.json"
    data = report.model_dump(mode="json")
    write_json(path, data)
    write_json(runs_dir / f"latest-{report.command}.json", data)
    return path


def load_latest(runs_dir: Path, command: str) -> dict | None:
    return read_json(runs_dir / f"latest-{command}.json")
