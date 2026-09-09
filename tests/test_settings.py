from pathlib import Path

import pytest

from sitemill.settings import Secrets, SecretsError, SiteConfig, Workspace

SITE_TOML = """
[site]
id = "demo"
name = "デモ"
base_url = "https://demo.example/"
service = "demo.service:service"

[operator]
name = "準備中"

[llm]
model = "claude-haiku-4-5"
"""


def test_site_config_load_and_user_agent(tmp_path: Path) -> None:
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    cfg = SiteConfig.load(tmp_path / "site.toml")
    assert cfg.base_url == "https://demo.example"
    assert cfg.url("/about/") == "https://demo.example/about/"
    assert (
        cfg.user_agent.startswith("sitemill/") and "+https://demo.example/about/" in cfg.user_agent
    )
    assert cfg.llm.model == "claude-haiku-4-5"
    assert cfg.operator.contact == "準備中"


def test_secrets_reads_env_file_and_rejects_op_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-test\nGOOGLE_MAPS_EMBED_KEY=\n", encoding="utf-8"
    )
    s = Secrets.load(tmp_path)
    assert s.anthropic_api_key == "sk-test"
    assert s.google_maps_embed_key is None
    assert s.require("anthropic_api_key") == "sk-test"
    with pytest.raises(SecretsError, match="GOOGLE_MAPS_EMBED_KEY"):
        s.require("google_maps_embed_key")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=op://dev/x/key\n", encoding="utf-8")
    with pytest.raises(ValueError, match="op://"):
        Secrets.load(tmp_path)


def test_workspace_open_finds_root_from_subdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    sub = tmp_path / "data" / "sources"
    sub.mkdir(parents=True)
    ws = Workspace.open(sub)
    assert ws.root == tmp_path.resolve()
    assert ws.raw_dir == tmp_path.resolve() / "data" / "raw"
    ws.ensure_dirs()
    assert ws.llm_cache_dir.is_dir()
