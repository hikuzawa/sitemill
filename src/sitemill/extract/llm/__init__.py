"""LLM プロバイダの組み立て。anthropic（既定）と fixture（テスト用）だけを持つ。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sitemill.extract.llm.anthropic_provider import AnthropicProvider
from sitemill.extract.llm.base import (
    LLMError,
    LLMProvider,
    LLMResult,
    request_key,
    supports_sampling,
)
from sitemill.extract.llm.cache import CachingProvider
from sitemill.extract.llm.fixture import FixtureProvider
from sitemill.settings import Secrets

__all__ = [
    "AnthropicProvider",
    "CachingProvider",
    "FixtureProvider",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "make_provider",
    "request_key",
    "supports_sampling",
]


def make_provider(
    name: str,
    *,
    secrets: Secrets | None = None,
    cache_dir: Path | None = None,
    fixture_responses: list[dict[str, Any]] | None = None,
) -> LLMProvider:
    """設定名からプロバイダを作る。鍵が無いときは SecretsError で止まり、.env に書く名前を示す。"""
    if name == "anthropic":
        if secrets is None:
            raise ValueError("anthropic プロバイダには secrets が必要")
        inner: LLMProvider = AnthropicProvider(secrets.require("anthropic_api_key"))
    elif name == "fixture":
        inner = FixtureProvider(fixture_responses)
    else:
        raise ValueError(f"未知の LLM プロバイダ: {name}（anthropic | fixture）")
    return CachingProvider(inner, cache_dir) if cache_dir is not None else inner
