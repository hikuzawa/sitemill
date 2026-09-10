"""Cloudflare Pages へのデプロイ。手元では dry-run（dist の検査）だけを行う（ADR 0007）。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 20_000
# _redirects の source は「/パス」またはドメイン単位（www.example.com/* など）を許す
_HOST_SOURCE = re.compile(r"^(?:https?://)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?/")


@dataclass
class DeployPlan:
    dist: Path
    files: int = 0
    total_bytes: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    deployed: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


def check_dist(dist: Path) -> DeployPlan:
    plan = DeployPlan(dist=dist)
    if not dist.is_dir():
        plan.problems.append(f"{dist} がない。先に build を実行する")
        return plan
    if not (dist / "index.html").is_file():
        plan.problems.append("dist/index.html がない")
    for path in dist.rglob("*"):
        if path.is_file():
            plan.files += 1
            size = path.stat().st_size
            plan.total_bytes += size
            if size > MAX_FILE_BYTES:
                plan.problems.append(f"{path.relative_to(dist)} が 25MB を超えている")
    if plan.files > MAX_FILES:
        plan.problems.append(f"ファイル数 {plan.files} が上限 {MAX_FILES} を超えている")
    redirects = dist / "_redirects"
    if redirects.is_file():
        for n, line in enumerate(redirects.read_text(encoding="utf-8").splitlines(), 1):
            parts = line.split()
            if not parts or line.startswith("#"):
                continue
            if len(parts) < 2 or not (parts[0].startswith("/") or _HOST_SOURCE.match(parts[0])):
                plan.problems.append(f"_redirects {n} 行目の書式が不正: {line!r}")
    plan.notes.append(f"{plan.files} files, {plan.total_bytes / 1024:.0f} KB")
    return plan


def deploy(
    dist: Path,
    *,
    project_name: str,
    account_id: str | None,
    api_token: str | None,
    dry_run: bool = True,
    branch: str | None = None,
) -> DeployPlan:
    """dry_run=False のときだけ wrangler で配置する。wrangler は Node 環境に要る。"""
    plan = check_dist(dist)
    if dry_run or not plan.ok:
        plan.notes.append("dry-run: 配置は行わない")
        return plan
    if not (account_id and api_token):
        plan.problems.append("CLOUDFLARE_ACCOUNT_ID と CLOUDFLARE_API_TOKEN を .env に書く")
        return plan
    npx = shutil.which("npx")
    if npx is None:
        plan.problems.append("npx が見つからない。Node.js と wrangler を入れるか CI で配置する")
        return plan
    cmd = [npx, "wrangler", "pages", "deploy", str(dist), f"--project-name={project_name}"]
    if branch:
        cmd.append(f"--branch={branch}")
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": account_id, "CLOUDFLARE_API_TOKEN": api_token}
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        plan.problems.append(f"wrangler が失敗: {proc.stderr.strip()[-500:]}")
    else:
        plan.deployed = True
        plan.notes.append(proc.stdout.strip()[-500:])
    return plan
