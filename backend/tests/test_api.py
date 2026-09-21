from app.main import get_llm_provider
from app.integrations.llm import LLMError


class FakeLLM:
    def __init__(self):
        self.received_messages = []

    async def chat(self, messages):
        self.received_messages = messages
        return "这是测试回答"


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_requires_login(client, app):
    fake = FakeLLM()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    response = client.post("/api/chat", json={"messages": [{"role": "user", "content": "你好"}]})
    assert response.status_code == 401
    assert not fake.received_messages


def test_chat_passes_full_conversation(logged_in, app):
    fake = FakeLLM()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    response = logged_in.post("/api/chat", json={"messages": [
        {"role": "user", "content": "我的名字是小明"},
        {"role": "assistant", "content": "你好，小明"},
        {"role": "user", "content": "我叫什么？"},
    ]})
    assert response.status_code == 200
    assert response.json()["content"] == "这是测试回答"
    assert len(fake.received_messages) == 3
    assert fake.received_messages[-1].content == "我叫什么？"


def test_chat_rejects_assistant_as_last_message(logged_in):
    response = logged_in.post("/api/chat", json={"messages": [{"role": "assistant", "content": "你好"}]})
    assert response.status_code == 422


def test_chat_returns_readable_model_error(logged_in, app):
    class FailingLLM:
        async def chat(self, messages):
            raise LLMError("后端尚未配置 DASHSCOPE_API_KEY")
    app.dependency_overrides[get_llm_provider] = FailingLLM
    response = logged_in.post("/api/chat", json={"messages": [{"role": "user", "content": "你好"}]})
    assert response.status_code == 503
    assert response.json() == {"detail": "后端尚未配置 DASHSCOPE_API_KEY"}
