from pathlib import Path
from types import SimpleNamespace

import pytest

from sitemill.extract.llm import (
    AnthropicProvider,
    CachingProvider,
    FixtureProvider,
    LLMError,
    make_provider,
    request_key,
    supports_sampling,
)
from sitemill.settings import Secrets, SecretsError


class _FakeStream:
    def __init__(self, response: object) -> None:
        self.response = response

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get_final_message(self) -> object:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def stream(self, **kwargs: object) -> _FakeStream:
        self.kwargs = kwargs
        return _FakeStream(self.response)


class _FakeClient:
    def __init__(self, response: object) -> None:
        self.messages = _FakeMessages(response)


def _response(text: str, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-haiku-4-5",
    )


COMMON = {"system": "S", "user": "U", "schema": {"type": "object"}, "max_tokens": 100}


def test_anthropic_provider_builds_structured_output_request() -> None:
    client = _FakeClient(_response('{"items": []}'))
    r = AnthropicProvider("sk-test", client=client).complete_json(
        model="claude-haiku-4-5", temperature=0.0, **COMMON
    )
    kw = client.messages.kwargs
    assert kw is not None
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["extra_body"] == {"temperature": 0.0}
    assert kw["messages"] == [{"role": "user", "content": "U"}]
    assert r.data == {"items": []} and r.input_tokens == 10 and r.provider == "anthropic"


def test_anthropic_provider_omits_temperature_for_models_without_sampling() -> None:
    client = _FakeClient(_response("{}"))
    AnthropicProvider("k", client=client).complete_json(
        model="claude-opus-5", temperature=0.0, **COMMON
    )
    assert client.messages.kwargs is not None and "extra_body" not in client.messages.kwargs
    assert supports_sampling("claude-haiku-4-5") and not supports_sampling("claude-sonnet-5")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response("{}", stop="refusal"), "拒否"),
        (_response("{}", stop="max_tokens"), "max_tokens"),
        (_response("not json"), "JSON"),
        (ValueError("Streaming is required"), "SDK"),
    ],
)
def test_anthropic_provider_raises_on_unusable_responses(response: object, message: str) -> None:
    with pytest.raises(LLMError, match=message):
        AnthropicProvider("k", client=_FakeClient(response)).complete_json(
            model="claude-haiku-4-5", temperature=None, **COMMON
        )


def test_fixture_provider_queue_and_by_key() -> None:
    key = request_key(
        provider="fixture", model="m", system="S", user="U", schema={"type": "object"}
    )
    p = FixtureProvider([{"q": 1}], by_key={key: {"k": 1}})
    assert p.complete_json(model="m", temperature=None, **COMMON).data == {"k": 1}
    assert p.complete_json(
        model="m", temperature=None, system="S", user="other", schema={}, max_tokens=1
    ).data == {"q": 1}
    with pytest.raises(LLMError, match="fixture"):
        p.complete_json(
            model="m", temperature=None, system="S", user="other", schema={}, max_tokens=1
        )


def test_caching_provider_reuses_same_input(tmp_path: Path) -> None:
    inner = FixtureProvider([{"a": 1}])
    c = CachingProvider(inner, tmp_path / "cache")
    r1 = c.complete_json(model="m", temperature=None, **COMMON)
    r2 = c.complete_json(model="m", temperature=None, **COMMON)
    assert r1.data == r2.data == {"a": 1}
    assert not r1.cached and r2.cached and len(inner.calls) == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    with pytest.raises(LLMError):
        c.complete_json(
            model="m", temperature=None, system="S", user="changed", schema={}, max_tokens=1
        )


def test_make_provider_requires_api_key_and_rejects_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    with pytest.raises(SecretsError, match="ANTHROPIC_API_KEY"):
        make_provider("anthropic", secrets=Secrets.load(tmp_path))
    with pytest.raises(ValueError, match="未知"):
        make_provider("openai")
    fixture = make_provider("fixture", cache_dir=tmp_path / "c")
    assert isinstance(fixture, CachingProvider) and fixture.name == "fixture"
    real = make_provider("anthropic", secrets=Secrets(anthropic_api_key="sk-test"))
    assert isinstance(real, AnthropicProvider)
