"""LLM プロバイダの共通インターフェース（ADR 0009）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    """LLM 呼び出しが使える結果を返さなかった。"""


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached: bool = False
    raw_text: str | None = None


class LLMProvider(Protocol):
    name: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        max_tokens: int,
        temperature: float | None,
    ) -> LLMResult: ...


def request_key(
    *, provider: str, model: str, system: str, user: str, schema: dict[str, Any]
) -> str:
    """同じ入力を同じ鍵にする。キャッシュと fixture の照合に使う。"""
    payload = json.dumps(
        {"provider": provider, "model": model, "system": system, "user": user, "schema": schema},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def supports_sampling(model: str) -> bool:
    """temperature を送ってよいモデルか。4.6 以降の世代は送ると 400 になる。"""
    m = model.lower()
    return any(tag in m for tag in ("haiku-4-5", "sonnet-4-5", "opus-4-5", "opus-4-1", "-3-"))
