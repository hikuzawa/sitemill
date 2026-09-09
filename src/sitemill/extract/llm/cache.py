"""同じ入力では LLM を呼び直さないためのファイルキャッシュ（git 管理外）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sitemill.extract.llm.base import LLMProvider, LLMResult, request_key
from sitemill.store.jsonio import read_json, write_json


class CachingProvider:
    def __init__(self, inner: LLMProvider, cache_dir: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.cache_dir = cache_dir

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        max_tokens: int,
        temperature: float | None,
    ) -> LLMResult:
        key = request_key(
            provider=self.inner.name, model=model, system=system, user=user, schema=schema
        )
        path = self.cache_dir / f"{key}.json"
        hit = read_json(path)
        if hit and "data" in hit:
            return LLMResult(
                data=hit["data"],
                model=hit.get("model", model),
                provider=self.inner.name,
                input_tokens=hit.get("input_tokens"),
                output_tokens=hit.get("output_tokens"),
                cached=True,
            )
        result = self.inner.complete_json(
            system=system,
            user=user,
            schema=schema,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        write_json(
            path,
            {
                "data": result.data,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            },
        )
        return result
