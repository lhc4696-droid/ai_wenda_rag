import pytest
import socket
from fastapi.testclient import TestClient

from app.config import Settings
from app.config_groups import EmailCodeConfig
from app.main import create_app, get_llm_provider
from tests.fakes import FakeMailer, register_user


@pytest.fixture
def app(tmp_path):
    settings = Settings(dashscope_api_key="", qwen_model="test-model", qwen_vision_model="", qwen_coding_model="", system_prompt="test", cors_origins=("http://127.0.0.1:5173", "http://localhost:5173"), auth_db_path=tmp_path / "auth.sqlite3", email_code=EmailCodeConfig(secret="test-only-secret-do-not-use-in-production-2026"))
    application = create_app(settings)
    application.state.mailer = FakeMailer()
    class NoExternalModel:
        async def chat(self, messages):
            raise AssertionError("测试必须显式注入模型替身，禁止访问真实模型服务")
    application.dependency_overrides[get_llm_provider] = NoExternalModel
    return application


@pytest.fixture(autouse=True)
def block_external_connections(monkeypatch):
    def check(address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("后端自动化测试禁止建立外部网络连接")
    original_create = socket.create_connection
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    def create(address, *args, **kwargs):
        check(address)
        return original_create(address, *args, **kwargs)
    def guarded(original):
        def connect(sock, address):
            if sock.family != getattr(socket, "AF_UNIX", None):
                check(address)
            return original(sock, address)
        return connect
    # 保留事件循环的本机 socketpair，禁止对模型/邮件服务器建立连接。
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket.socket, "connect", guarded(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(original_connect_ex))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client):
    response = register_user(client, {"username": "test_user", "password": "Test-password-2026!"})
    assert response.status_code == 201
    return client
