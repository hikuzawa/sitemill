"""保存済みの応答を返すプロバイダ。テストと eval で外部アクセスなしに抽出を回す。"""

from __future__ import annotations

from collections import deque
from typing import Any

from sitemill.extract.llm.base import LLMError, LLMResult, request_key


class FixtureProvider:
    name = "fixture"

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        *,
        by_key: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._queue: deque[dict[str, Any]] = deque(responses or [])
        self._by_key = by_key or {}
        self.calls: list[dict[str, Any]] = []

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
        key = request_key(provider=self.name, model=model, system=system, user=user, schema=schema)
        self.calls.append({"key": key, "model": model, "user_chars": len(user)})
        if key in self._by_key:
            data = self._by_key[key]
        elif self._queue:
            data = self._queue.popleft()
        else:
            raise LLMError(
                "fixture 応答がありません（tests/fixtures の llm_response.json を用意する）"
            )
        return LLMResult(
            data=data, model=model, provider=self.name, input_tokens=0, output_tokens=0
        )
