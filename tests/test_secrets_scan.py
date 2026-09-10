import subprocess
from pathlib import Path

from sitemill.secrets_scan import (
    blocked_filename,
    mask,
    scan_diff,
    scan_history,
    scan_paths,
    scan_staged,
    scan_text,
)

FAKE_ANTHROPIC = "sk-ant-api03-" + "A" * 40
FAKE_GITHUB = "ghp_" + "b" * 36


def test_patterns_detect_keys_and_mask_values() -> None:
    text = f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\ntoken = '{FAKE_GITHUB}'\n"
    findings = scan_text("x.txt", text)
    rules = {f.rule for f in findings}
    assert "anthropic_api_key" in rules and "github_token" in rules
    assert all(FAKE_ANTHROPIC not in str(f) for f in findings)
    assert mask(FAKE_ANTHROPIC).startswith("sk-ant") and "53 文字" in mask(FAKE_ANTHROPIC)


def test_placeholders_and_code_are_not_flagged() -> None:
    ok = "\n".join(
        [
            "ANTHROPIC_API_KEY=",
            "api_key = os.environ['ANTHROPIC_API_KEY']",
            "token: ${{ secrets.CLOUDFLARE_API_TOKEN }}",
            "ANTHROPIC_API_KEY=op://dev-secrets/x/key",
            'Secrets(anthropic_api_key="sk-test")',
            'data-cf-beacon=\'{"token": "{escape(token)}"}\'',
            '"content_hash": "sha256:0123456789abcdef0123456789abcdef"',
        ]
    )
    assert scan_text("code.py", ok) == []


def test_env_example_must_hold_placeholders_only() -> None:
    assert scan_text(".env.example", "ANTHROPIC_API_KEY=\nX=<値>\n# c\n") == []
    bad = scan_text(
        "akiya-atlas/.env.example", "GOOGLE_MAPS_EMBED_KEY=AIzaSyRealLookingValue000000000000000\n"
    )
    assert {f.rule for f in bad} >= {"env_example_value"}


def test_blocked_filenames() -> None:
    assert (
        blocked_filename(".env")
        and blocked_filename("sub/.env.local")
        and blocked_filename("k.pem")
    )
    assert not blocked_filename(".env.example") and not blocked_filename("src/env.py")


def test_scan_diff_reads_added_lines_only() -> None:
    diff = (
        "+++ b/notes.md\n"
        f"-old {FAKE_ANTHROPIC}\n"
        "+new line without secrets\n"
        "+++ b/.env\n"
        "+ANTHROPIC_API_KEY=\n"
    )
    findings = scan_diff(diff)
    assert [f.rule for f in findings] == ["blocked_filename"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_staged_and_history_scans_in_temp_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "ok.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "ok.txt")
    assert scan_staged(tmp_path) == []
    _git(tmp_path, "commit", "-q", "-m", "ok")

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=x\n", encoding="utf-8")
    (tmp_path / "leak.md").write_text(f"key {FAKE_ANTHROPIC}\n", encoding="utf-8")
    _git(tmp_path, "add", "-f", ".env", "leak.md")
    rules = sorted(f.rule for f in scan_staged(tmp_path))
    assert rules == ["anthropic_api_key", "blocked_filename"]
    _git(tmp_path, "commit", "-q", "-m", "leak")
    hist = {f.rule for f in scan_history(tmp_path)}
    assert {"anthropic_api_key", "blocked_filename"} <= hist

    (tmp_path / ".env").unlink()
    findings = scan_paths([tmp_path])
    assert any(f.rule == "anthropic_api_key" for f in findings)
