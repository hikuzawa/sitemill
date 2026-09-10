"""Anthropic Claude による構造化出力。値ではなく引用を返させる前提で使う（ADR 0004, 0009）。"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from sitemill.extract.llm.base import LLMError, LLMResult, supports_sampling


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, client: Any | None = None) -> None:
        if client is None:
            client = anthropic.Anthropic(api_key=api_key)
        self._client = client

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
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # 指示は毎回同じなのでキャッシュ対象にする。ページ本文は user 側で毎回変わる
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        # SDK 1.x は temperature を型付き引数に持たないので extra_body で渡す。
        # 4.6 以降の世代のモデルには送らない（400 になる）
        if temperature is not None and supports_sampling(model):
            kwargs["extra_body"] = {"temperature": temperature}
        try:
            # 一覧ページは出力が長くなるため常にストリーミングで受け、最終メッセージだけを使う
            with self._client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise LLMError(f"API エラー {e.status_code}: {str(e)[:300]}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"接続エラー: {str(e)[:300]}") from e
        except ValueError as e:  # SDK 側の事前検証（例: 非ストリーミングでの長すぎる max_tokens）
            raise LLMError(f"SDK エラー: {str(e)[:300]}") from e
        if resp.stop_reason == "refusal":
            raise LLMError("LLM が応答を拒否しました（stop_reason=refusal）")
        if resp.stop_reason == "max_tokens":
            raise LLMError(
                "出力が max_tokens に達しました。max_output_tokens を増やすか入力を分割する"
            )
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise LLMError("LLM 応答にテキストブロックがありません")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM 応答が JSON ではありません: {e}") from e
        usage = getattr(resp, "usage", None)
        return LLMResult(
            data=data,
            model=getattr(resp, "model", model),
            provider=self.name,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            raw_text=text,
        )
