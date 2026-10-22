"""User-run offline regressions. All model requests and DNS answers are mocked."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.attachments.context import ModelMessage
from app.coding import compatible_model
from app.coding.schemas import CodingError
from app.integrations import compatible
from app.integrations.compatible_transport import (
    PublicResolver, normalize_base_url, post_completion, usage_count,
)
from app.integrations.llm import LLMError


@pytest.mark.parametrize("address", [
    "http://api.example.com/v1", "https://localhost/v1", "https://foo.local/v1",
    "https://127.0.0.1/v1", "https://10.0.0.1/v1", "https://169.254.169.254/v1",
    "https://100.64.0.1/v1", "https://224.0.0.1/v1", "https://[::1]/v1",
    "https://[::ffff:127.0.0.1]/v1", "https://[64:ff9b::7f00:1]/v1",
    "https://[2002:7f00:1::]/v1", "https://2130706433/v1",
    "https://user:secret@api.example.com/v1", "https://api.example.com/v1?api_key=secret",
    "https://api.example.com/v1#fragment", "https://api.example.com/%2e%2e/v1",
    "https://api.example.com/v1/../admin", "https://api.example.com//admin",
    "https://api.example.com/v1/chat/completions", "https://api.example.com:/v1",
    "https://api.example.com\\@localhost/v1", "https://api.example.com/v1\nsecret",
])
def test_unsafe_base_urls_are_rejected(address):
    with pytest.raises(ValueError) as error:
        normalize_base_url(address)
    assert address not in str(error.value)
    assert "secret" not in str(error.value)


def test_base_url_normalization_preserves_provider_prefix():
    assert normalize_base_url(" https://API.EXAMPLE.COM:443/compatible-mode/v1/ ") == "https://api.example.com/compatible-mode/v1"
    assert normalize_base_url("https://api.example.com:8443/v1") == "https://api.example.com:8443/v1"


def test_resolver_checks_all_answers_at_connect_time():
    resolver = PublicResolver.__new__(PublicResolver)
    resolver._resolver = SimpleNamespace(resolve=AsyncMock(return_value=[
        {"host": "8.8.8.8"}, {"host": "127.0.0.1"},
    ]))
    with pytest.raises(LLMError) as error:
        asyncio.run(resolver.resolve("api.example.com", 443))
    assert error.value.code == "LLM_ENDPOINT_BLOCKED"
    resolver._resolver.resolve.assert_awaited_once_with("api.example.com", 443, 2)


def profile():
    return SimpleNamespace(base_url="https://api.example.com/v1", api_key="dummy-user-key",
                           chat_model="chat-demo", coding_model="code-demo", vision_model="vision-demo", max_output_tokens=None)


def settings():
    return SimpleNamespace(system_prompt="You are helpful.", chat_timeout=30, chat_max_output_tokens=1000,
                           custom_model_proxy_url="", custom_model_proxy_base_urls=())


def reply(content="Hello", calls=None, reason="stop"):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8}}


def test_chat_uses_account_model_and_does_not_put_key_in_payload(monkeypatch):
    sender = AsyncMock(return_value=reply())
    monkeypatch.setattr(compatible, "post_completion", sender)
    model = compatible.OpenAICompatibleLLM(settings(), profile())
    response = asyncio.run(model.chat([ModelMessage("user", "Hi")]))
    assert response.model == "chat-demo"
    assert (response.input_tokens, response.output_tokens) == (15, 8)
    endpoint, key, payload, timeout = sender.call_args.args
    assert endpoint == "https://api.example.com/v1"
    assert key == "dummy-user-key"
    assert "dummy-user-key" not in str(payload)
    assert payload["messages"][-1] == {"role": "user", "content": "Hi"}
    assert "不可信资料" in payload["messages"][0]["content"]


def test_missing_vision_model_fails_before_request(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(compatible, "post_completion", sender)
    config = profile()
    config.vision_model = ""
    model = compatible.OpenAICompatibleLLM(settings(), config)
    with pytest.raises(LLMError) as error:
        asyncio.run(model.chat([ModelMessage("user", "Describe", [object()])]))
    assert error.value.code == "VISION_NOT_CONFIGURED"
    sender.assert_not_called()


def test_image_uses_selected_vision_model_without_remote_image_urls(monkeypatch):
    sender = AsyncMock(return_value=reply("A picture"))
    monkeypatch.setattr(compatible, "post_completion", sender)
    monkeypatch.setattr(compatible, "_read_image", lambda path: "data:image/jpeg;base64,AA==")
    response = asyncio.run(compatible.OpenAICompatibleLLM(settings(), profile()).chat([
        ModelMessage("user", "Describe", [object()]),
    ]))
    payload = sender.call_args.args[2]
    assert response.model == payload["model"] == "vision-demo"
    assert payload["messages"][-1]["content"][1] == {
        "type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="},
    }


def call(identifier="call-1"):
    return {"id": identifier, "type": "function", "function": {"name": "read_file", "arguments": '{"path":"main.py"}'}}


def test_coding_preserves_existing_tool_protocol(monkeypatch):
    sender = AsyncMock(return_value=reply(None, [call()], "tool_calls"))
    monkeypatch.setattr(compatible_model, "post_completion", sender)
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    result = asyncio.run(compatible_model.OpenAICompatibleCodingModel(settings(), profile(), tools).call([]))
    assert result.tool_calls == [call()]
    payload = sender.call_args.args[2]
    assert payload["model"] == "code-demo"
    assert payload["tools"] == tools
    assert payload["parallel_tool_calls"] is False
    assert "enable_thinking" not in payload


@pytest.mark.parametrize("answer", [
    reply(None, [call(), call()]), reply("", []), reply("x" * 40001),
    reply(None, [call(str(index)) for index in range(5)]),
    reply("partial", reason="length"),
    reply(None, [{"id": "x", "function": {"name": "read_file", "arguments": {}}}]),
])
def test_coding_rejects_unsafe_or_truncated_responses(monkeypatch, answer):
    monkeypatch.setattr(compatible_model, "post_completion", AsyncMock(return_value=answer))
    with pytest.raises(CodingError):
        asyncio.run(compatible_model.OpenAICompatibleCodingModel(settings(), profile(), []).call([]))


def test_invalid_endpoint_rejected_before_any_network():
    with pytest.raises(LLMError) as error:
        asyncio.run(post_completion("https://127.0.0.1/v1", "dummy-key", {}, 30))
    assert error.value.code == "LLM_ENDPOINT_INVALID"


@pytest.mark.parametrize("value", [True, -1, 1.5, "15", 10 ** 100])
def test_untrusted_usage_cannot_overflow_accounting(value):
    assert usage_count({"usage": {"prompt_tokens": value}}, "prompt_tokens") is None
