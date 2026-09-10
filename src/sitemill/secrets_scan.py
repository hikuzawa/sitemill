"""秘密らしき文字列の検出。コミット前（--staged）、全履歴（--history）、作業ツリーの走査に使う。

再発防止の要点:
- .env / 鍵ファイルはファイル名で止める（.env.example だけ許可し、その中は空の値だけを認める）
- API キーやトークンの形をした文字列を追加行から見つけたら止める
- 値は画面に出さず、先頭数文字と長さだけ示す
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}")),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    (
        "cloudflare_token",
        re.compile(r"CLOUDFLARE_API_TOKEN\s*[=:]\s*['\"]?([A-Za-z0-9_-]{30,})"),
    ),
    (
        "generic_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d)\b\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{24,})"
        ),
    ),
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:<.*>|\$\{\{.*|op://.*|\.\.\.|x{3,}|your[_-].*|example.*|changeme|dummy.*|"
    r"sk-test|secrets\..*|none|null)$"
)
_BLOCKED_NAME = re.compile(
    r"(?:^|/)(?:\.env(?:\..+)?|[^/]*\.(?:pem|p12|pfx|key)|id_rsa|id_ed25519)$"
)
_ALLOWED_BASENAMES = frozenset({".env.example"})
_ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}
)


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None
    masked: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"[{self.rule}] {where} {self.masked}"


def mask(value: str) -> str:
    """値そのものは出さない。先頭 6 文字と長さだけ示す。"""
    return f"{value[:6]}…({len(value)} 文字)"


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip("'\"")
    return v == "" or bool(_PLACEHOLDER.match(v))


def scan_line(path: str, lineno: int | None, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for rule, pattern in PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(1) if m.groups() and m.group(1) else m.group(0)
            if rule in ("generic_assignment", "cloudflare_token") and _is_placeholder(value):
                continue
            findings.append(Finding(rule, path, lineno, mask(value)))
    if Path(path).name in _ALLOWED_BASENAMES:
        m = _ENV_ASSIGN.match(text)
        if m and not _is_placeholder(m.group(2)):
            findings.append(
                Finding("env_example_value", path, lineno, f"{m.group(1)}= に値が入っている")
            )
    return findings


def blocked_filename(path: str) -> bool:
    name = Path(path).name
    return name not in _ALLOWED_BASENAMES and bool(_BLOCKED_NAME.search(path.replace("\\", "/")))


def scan_text(path: str, text: str, *, added_only: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        findings.extend(scan_line(path, i, line))
    return findings


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def scan_diff(diff: str) -> list[Finding]:
    """unified diff の追加行だけを走査する。"""
    findings: list[Finding] = []
    path = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            if path != "/dev/null" and blocked_filename(path):
                findings.append(Finding("blocked_filename", path, None, "鍵や .env のファイル"))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            findings.extend(scan_line(path, None, line[1:]))
    return findings


def scan_staged(repo: Path) -> list[Finding]:
    names = [
        n
        for n in _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
        if n
    ]
    findings = [
        Finding("blocked_filename", n, None, "鍵や .env のファイルはコミットしない")
        for n in names
        if blocked_filename(n)
    ]
    diff = _git(repo, "diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR")
    findings.extend(f for f in scan_diff(diff) if f.rule != "blocked_filename")
    return findings


def scan_history(repo: Path) -> list[Finding]:
    """全ブランチ・全コミットの追加行を走査する（公開前の最終確認向け）。"""
    added_names = _git(
        repo, "log", "--all", "--name-only", "--diff-filter=A", "--format="
    ).splitlines()
    findings = [
        Finding("blocked_filename", n, None, "鍵や .env のファイルが履歴に含まれる")
        for n in sorted({n for n in added_names if n and blocked_filename(n)})
    ]
    diff = _git(repo, "log", "-p", "--all", "--unified=0", "--no-color")
    # env_example_value は「これから入れるコミット」を止める規則。過去の履歴には適用しない
    findings.extend(
        f for f in scan_diff(diff) if f.rule not in ("blocked_filename", "env_example_value")
    )
    return findings


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in files:
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            rel = str(f)
            if blocked_filename(rel):
                findings.append(Finding("blocked_filename", rel, None, "鍵や .env のファイル"))
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            findings.extend(scan_text(rel, text))
    return findings
