"""Offline adapter checks: no database, real credentials, or provider requests."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from dashscope.api_entities.dashscope_response import DashScopeAPIResponse, MultiModalConversationResponse

from app.coding.model import CodingModel
from app.coding.schemas import CodingError
from app.integrations.dashscope_messages import multimodal_messages
from app.integrations.llm import DashScopeLLM, LLMError


@pytest.fixture
def settings():
    return SimpleNamespace(
        dashscope_api_key="fake-offline-key", qwen_model="qwen3.8-27b",
        qwen_coding_model="", qwen_vision_model="test-vision", system_prompt="test",
        chat_timeout=90, chat_max_output_tokens=2048,
    )


@pytest.fixture
def sdk(monkeypatch):
    import aiohttp
    import dashscope

    state = {"message": {"content": [{"text": "hello"}, {"text": "world"}]},
             "status": 200, "finish_reason": "stop"}

    class Session:
        def __init__(self, **kwargs):
            state["trust_env"] = kwargs["trust_env"]
        async def __aenter__(self): return self
        async def __aexit__(self, *args): state["closed"] = True

    async def call(**kwargs):
        state["request"] = kwargs
        return MultiModalConversationResponse.from_api_response(DashScopeAPIResponse(
            status_code=state["status"], code="test", message="private provider error",
            output={"choices": [{"message": {"role": "assistant", **state["message"]}, "finish_reason": state["finish_reason"]}]},
            usage={"input_tokens": 12, "output_tokens": 4},
        ))

    async def wrong_endpoint(**kwargs):
        raise AssertionError("Text endpoint must not be used")

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(dashscope.AioMultiModalConversation, "call", call)
    monkeypatch.setattr(dashscope.AioGeneration, "call", wrong_endpoint)
    return state


def test_chat_text_and_history(settings, sdk):
    messages = [SimpleNamespace(role=role, content=text) for role, text in
                [("user", "first"), ("assistant", "answer"), ("user", "next")]]
    reply = asyncio.run(DashScopeLLM(settings).chat(messages))
    assert reply.content == "hello\nworld"
    assert reply.input_tokens == 12 and reply.output_tokens == 4
    request = sdk["request"]
    assert request["messages"][1:] == [
        {"role": m.role, "content": [{"text": m.content}]} for m in messages]
    assert request["enable_thinking"] is False
    assert request["model"] == "qwen3.8-27b"
    assert request["max_tokens"] == 2048 and request["request_timeout"] == 90
    assert sdk["closed"] and sdk["trust_env"]


def test_chat_image_payload(settings, sdk):
    photo = SimpleNamespace(read_bytes=lambda: b"test-image")
    asyncio.run(DashScopeLLM(settings).chat([
        SimpleNamespace(role="user", content="describe", images=[photo])]))
    assert sdk["request"]["model"] == "test-vision"
    content = sdk["request"]["messages"][1]["content"]
    assert content[0]["image"].startswith("data:image/jpeg;base64,")
    assert content[1] == {"text": "describe"}


def test_coding_tool_round_trip_preserves_history(settings, sdk):
    tool = {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"demo.py"}'}}
    messages = [{"role": "system", "content": "test"},
                {"role": "user", "content": "read demo"},
                {"role": "assistant", "content": "", "tool_calls": [tool]},
                {"role": "tool", "tool_call_id": "call-1", "content": '{"text":"demo"}'}]
    original = deepcopy(messages)
    sdk["message"] = {"content": [], "tool_calls": [tool]}
    model = CodingModel(settings, [])
    reply = asyncio.run(model.call(messages))
    assert reply.content == "" and reply.tool_calls == [tool]
    assert reply.input_tokens == 12
    payload = sdk["request"]["messages"]
    assert payload[2]["content"] == []
    assert payload[2]["tool_calls"] == [tool]
    assert payload[3] == original[3]
    payload[2]["tool_calls"][0]["function"]["arguments"] = "changed"
    assert messages == original
    sdk["message"] = {"content": [{"text": "done"}]}
    assert asyncio.run(model.call(messages)).content == "done"


@pytest.mark.parametrize("content", [[{"text": 42}], [{"image": "unexpected"}], "x" * 40001],
                         ids=["invalid-text", "unexpected-image", "oversize"])
def test_coding_rejects_invalid_or_oversize_content(settings, sdk, content):
    sdk["message"] = {"content": content}
    with pytest.raises(CodingError) as error:
        asyncio.run(CodingModel(settings).call([]))
    assert error.value.code == "CODING_MODEL_INVALID"


def test_coding_truncation_is_still_rejected(settings, sdk):
    sdk["finish_reason"] = "length"
    with pytest.raises(CodingError) as error:
        asyncio.run(CodingModel(settings).call([]))
    assert error.value.code == "CODING_MODEL_TRUNCATED"


def test_errors_do_not_expose_provider_body(settings, sdk):
    sdk["status"] = 403
    for adapter, method, error_class in [(DashScopeLLM(settings), "chat", LLMError),
                                         (CodingModel(settings), "call", CodingError)]:
        with pytest.raises(error_class) as error:
            asyncio.run(getattr(adapter, method)([]))
        assert "private provider" not in str(error.value)


def test_existing_multimodal_and_null_content_are_supported():
    original = [{"role": "user", "content": [{"text": "hello"}]},
                {"role": "assistant", "content": None, "tool_calls": []}]
    result = multimodal_messages(original)
    assert result[0] == original[0] and result[1]["content"] == []
    assert original[1]["content"] is None
