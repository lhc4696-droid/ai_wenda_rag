"""Provider and wire regressions with actual parsers and offline SDK/HTTP doubles."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.integrations.llm import DashScopeLLM, LLMError, LLMResult
from app.integrations.compatible import OpenAICompatibleLLM
from app.integrations.compatible_transport import post_completion_stream, MAX_RESPONSE_BYTES
from app.chat.service import ChatService
from tests.test_model_proxy import wire, BASE, OPTIONS


async def collect(stream):
    return [item async for item in stream]


def setup():
    settings = SimpleNamespace(dashscope_api_key="offline", system_prompt="test", qwen_model="chat-demo",
                               qwen_vision_model="vision-demo", chat_timeout=10, chat_max_output_tokens=2048,
                               custom_model_proxy_url=OPTIONS["proxy_url"], custom_model_proxy_base_urls=(BASE,))
    profile = SimpleNamespace(base_url=BASE, api_key="offline", chat_model="chat-demo", vision_model="vision-demo", max_output_tokens=None)
    return settings, profile


def test_dashscope_uses_incremental_text_and_usage(monkeypatch):
    import dashscope
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse, MultiModalConversationResponse
    settings, _ = setup()
    request = {}

    async def call(**kwargs):
        request.update(kwargs)
        async def stream():
            for content, reason, usage in [("你", "null", {}), ("好", "stop", {"input_tokens": 10, "output_tokens": 2})]:
                yield MultiModalConversationResponse.from_api_response(DashScopeAPIResponse(
                    status_code=200, output={"choices": [{"message": {"role": "assistant", "content": [{"text": content}]},
                                                          "finish_reason": reason}]}, usage=usage))
        return stream()
    monkeypatch.setattr(dashscope.AioMultiModalConversation, "call", call)
    chunks = asyncio.run(collect(DashScopeLLM(settings).stream_chat([SimpleNamespace(role="user", content="问候")])) )
    assert all(isinstance(item, LLMResult) for item in chunks)
    assert "".join(item.content for item in chunks) == "你好"
    assert chunks[0].finish_reason is None
    assert chunks[-1].input_tokens == 10 and chunks[-1].output_tokens == 2
    assert chunks[-1].finish_reason == "stop"
    assert request["incremental_output"] is True
    assert request["messages"][-1]["content"] == [{"text": "问候"}]
    visible = []
    async def emit(delta):
        visible.append(delta)
    answer = asyncio.run(ChatService.generate_stream(DashScopeLLM(settings),
        [SimpleNamespace(role="user", content="问候")], emit))
    assert visible == ["你", "好"]
    assert answer.content == "你好" and answer.finish_reason == "stop"


def test_dashscope_stream_error_is_failure_not_silent_end(monkeypatch):
    import dashscope
    settings, _ = setup()
    async def call(**kwargs):
        async def stream():
            yield SimpleNamespace(status_code=403, message="private key", output=None)
        return stream()
    monkeypatch.setattr(dashscope.AioMultiModalConversation, "call", call)
    with pytest.raises(LLMError) as caught:
        asyncio.run(collect(DashScopeLLM(settings).stream_chat([])))
    assert caught.value.code == "LLM_AUTH_FAILED"
    assert "private key" not in str(caught.value)


def payload(content="你好🙂", reason=None):
    return {"choices": [{"delta": {"content": content}, "finish_reason": reason}]}


def sse(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def test_compatible_stream_handles_byte_boundaries_metadata_and_secure_proxy(wire):
    settings, profile = setup()
    raw = b": ping\r\n\r\n" + sse(payload()) + sse(payload("", "length")) + sse({
        "choices": [], "usage": {"prompt_tokens": 15, "completion_tokens": 8}}) + b"data: [DONE]\n\n"
    wire.chunks = [bytes([byte]) for byte in raw]
    chunks = asyncio.run(collect(OpenAICompatibleLLM(settings, profile).stream_chat([SimpleNamespace(role="user", content="hello")])))
    assert "".join(item.content for item in chunks) == "你好🙂"
    assert any(item.finish_reason == "length" for item in chunks)
    assert chunks[-1].input_tokens == 15 and chunks[-1].output_tokens == 8
    assert wire.sessions[0]["trust_env"] is False
    assert wire.connectors[0]["ssl"] is True
    assert wire.posts[0][1]["proxy"] == OPTIONS["proxy_url"]
    assert wire.posts[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("raw", [b'data: {broken}\n\n', b'data: {"error":{"message":"private-key"}}\n\n',
                               b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'])
def test_bad_or_incomplete_upstream_never_completes(wire, raw):
    settings, profile = setup()
    wire.chunks = [raw]
    with pytest.raises(LLMError) as caught:
        asyncio.run(collect(OpenAICompatibleLLM(settings, profile).stream_chat([])))
    assert "private-key" not in str(caught.value)


def test_stream_has_response_byte_limit(wire):
    wire.chunks = [b"x" * (MAX_RESPONSE_BYTES + 1)]
    with pytest.raises(LLMError) as caught:
        asyncio.run(collect(post_completion_stream(BASE, "offline", {}, 10, **OPTIONS)))
    assert caught.value.code == "LLM_RESPONSE_TOO_LARGE"
