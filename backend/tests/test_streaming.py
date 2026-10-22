"""Streaming HTTP, real history transactions and accounting; no provider network."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.main import get_llm_provider
from app.integrations.llm import LLMError, LLMResult
from app.chat.streaming import conversation_events
from app.conversations.schemas import SendMessage


class StreamingModel:
    def __init__(self):
        self.calls = []
        self.error = None
        self.reason = "stop"
        self.parts = ["你好", "，世界"]
        self.finished = True

    async def chat(self, messages):
        raise AssertionError("流式接口必须真正读取增量，不能等待完整 chat 回答")

    async def stream_chat(self, messages):
        self.calls.append([(m.role, m.content) for m in messages])
        for part in self.parts:
            yield LLMResult(part)
        if self.error:
            raise self.error
        if self.finished:
            yield LLMResult("", 12, 4, self.reason, "stream-test")


@pytest.fixture
def streaming_model(app):
    model = StreamingModel()
    app.dependency_overrides[get_llm_provider] = lambda: model
    return model


def new_turn(client, **changes):
    conversation = client.post("/api/conversations", json={"id": uuid4().hex}).json()
    data = {"content": "你好", "client_message_id": uuid4().hex,
            "expected_last_sequence": 0, "source_mode": "chat", **changes}
    return conversation["id"], data


def events(client, identifier, data):
    response = client.post(f"/api/conversations/{identifier}/messages/stream", json=data)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_deltas_are_saved_once_replayed_and_ordered(logged_in, streaming_model, app):
    identifier, data = new_turn(logged_in)
    result = events(logged_in, identifier, data)
    assert [item["type"] for item in result] == ["start", "content", "content", "done"]
    assert "".join(item["delta"] for item in result if item["type"] == "content") == "你好，世界"
    completed = result[-1]["result"]
    assert completed["assistant_message"]["content"] == "你好，世界"
    assert completed["assistant_message"]["model"] == "stream-test"
    assert completed["user_message"]["status"] == "completed"
    replay = events(logged_in, identifier, data)[-1]["result"]
    assert replay["replayed"] is True
    assert replay["assistant_message"]["id"] == completed["assistant_message"]["id"]
    assert len(streaming_model.calls) == 1
    next_data = {**data, "content": "继续", "client_message_id": uuid4().hex, "expected_last_sequence": 2}
    assert events(logged_in, identifier, next_data)[-1]["type"] == "done"
    assert streaming_model.calls[-1] == [("user", "你好"), ("assistant", "你好，世界"), ("user", "继续")]
    with app.state.auth_store.connect() as db:
        calls = db.execute("SELECT state,input_tokens,output_tokens FROM model_calls").fetchall()
    assert [tuple(row) for row in calls] == [("succeeded", 12, 4), ("succeeded", 12, 4)]


@pytest.mark.parametrize("error", [LLMError("模型暂时不可用", "LLM_UNAVAILABLE"), RuntimeError("private-key-secret")])
def test_partial_failure_is_not_saved_as_completed_and_retry_reuses_question(logged_in, streaming_model, error):
    identifier, data = new_turn(logged_in)
    streaming_model.error = error
    failed = events(logged_in, identifier, data)
    assert failed[-1]["type"] == "error"
    assert "private-key-secret" not in json.dumps(failed)
    history = logged_in.get(f"/api/conversations/{identifier}/messages").json()
    assert len(history["messages"]) == 1
    assert history["messages"][0]["status"] == "failed"
    streaming_model.error = None
    done = events(logged_in, identifier, {**data, "retry": True})[-1]["result"]
    assert done["user_message"]["id"] == history["messages"][0]["id"]
    assert len(logged_in.get(f"/api/conversations/{identifier}/messages").json()["messages"]) == 2


def test_truncation_is_bounded_and_visible(logged_in, streaming_model):
    identifier, data = new_turn(logged_in)
    streaming_model.parts = ["字" * 19999, "尾" * 50]
    result = events(logged_in, identifier, data)
    content = "".join(item["delta"] for item in result if item["type"] == "content")
    answer = result[-1]["result"]["assistant_message"]
    assert len(content) == len(answer["content"]) == 20000
    assert answer["answer_truncated"] is True


def test_model_length_finish_is_marked(logged_in, streaming_model):
    identifier, data = new_turn(logged_in)
    streaming_model.reason = "length"
    assert events(logged_in, identifier, data)[-1]["result"]["assistant_message"]["answer_truncated"] is True


def test_stream_timeout_is_reported_and_releases_quota(logged_in, streaming_model, app):
    identifier, data = new_turn(logged_in)
    streaming_model.error = TimeoutError()
    failed = events(logged_in, identifier, data)[-1]
    assert failed["status"] == 504 and failed["error_code"] == "LLM_TIMEOUT"
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "timed_out"
    streaming_model.error = None
    assert events(logged_in, identifier, {**data, "retry": True})[-1]["type"] == "done"


def test_unfinished_upstream_is_failure(logged_in, streaming_model):
    identifier, data = new_turn(logged_in)
    streaming_model.finished = False
    assert events(logged_in, identifier, data)[-1]["type"] == "error"
    assert logged_in.get(f"/api/conversations/{identifier}/messages").json()["messages"][-1]["status"] == "failed"


def test_stream_obeys_quota_and_ownership(logged_in, streaming_model, app):
    identifier, data = new_turn(logged_in)
    assert events(logged_in, uuid4().hex, data)[-1]["error_code"] == "CONVERSATION_NOT_FOUND"
    assert not streaming_model.calls
    settings = app.state.conversation_service.chat.settings
    app.state.conversation_service.chat.settings = replace(settings, chat=replace(settings.chat, requests_per_day=1))
    assert events(logged_in, identifier, data)[-1]["type"] == "done"
    second, question = new_turn(logged_in)
    failure = events(logged_in, second, question)[-1]
    assert failure["error_code"] == "CHAT_DAILY_LIMIT"
    assert failure["retry_after"] > 0
    assert len(streaming_model.calls) == 1


def test_stream_rejects_unauthenticated_and_cross_site(client, logged_in):
    identifier, data = new_turn(logged_in)
    assert logged_in.post(f"/api/conversations/{identifier}/messages/stream", json=data,
                          headers={"Origin": "https://evil.invalid"}).status_code == 403
    logged_in.post("/api/auth/logout")
    assert client.post(f"/api/conversations/{identifier}/messages/stream", json=data).status_code == 401


def test_disconnect_cancels_model_and_releases_history_and_quota(logged_in, app):
    identifier, data = new_turn(logged_in)
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    closed = []

    class WaitingModel:
        async def stream_chat(self, messages):
            try:
                yield LLMResult("未完成")
                await asyncio.sleep(3600)
            finally:
                closed.append(True)

    async def disconnect():
        stream = conversation_events(app.state.conversation_service, owner, identifier,
                                     SendMessage(**data), "disconnect-test", WaitingModel())
        assert '"start"' in await anext(stream)
        assert '"content"' in await anext(stream)
        await stream.aclose()

    asyncio.run(disconnect())
    assert closed == [True]
    history = logged_in.get(f"/api/conversations/{identifier}/messages").json()["messages"]
    assert len(history) == 1 and history[0]["status"] == "failed"
    assert history[0]["error_code"] == "REQUEST_CANCELLED"
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT state FROM model_calls WHERE request_id='disconnect-test'").fetchone()[0] == "cancelled"


def test_stream_uses_attachment_preparation_and_rag_sources(logged_in, streaming_model, app):
    from tests.test_rag import FakeEmbedding, FakeVectors
    settings = app.state.settings
    settings = replace(settings, rag=replace(settings.rag, enabled=True))
    app.state.conversation_service.settings = settings
    app.state.rag.settings = settings
    app.state.rag.embedding = FakeEmbedding()
    app.state.rag.vectors = FakeVectors()
    uploaded = logged_in.post("/api/attachments", files={"file": ("notes.txt", "课程目标是掌握向量检索。".encode(), "text/plain")})
    assert uploaded.status_code == 201
    attachment = uploaded.json()["id"]
    identifier, data = new_turn(logged_in, source_mode="documents", attachment_ids=[attachment])
    done = events(logged_in, identifier, data)[-1]["result"]
    assert done["assistant_message"]["sources"][0]["attachment_id"] == attachment
    assert "课程目标是掌握向量检索" in streaming_model.calls[-1][-1][1]
    assert len(app.state.rag.embedding.calls) == 2
    assert events(logged_in, identifier, data)[-1]["result"]["replayed"] is True
    assert len(app.state.rag.embedding.calls) == 2
