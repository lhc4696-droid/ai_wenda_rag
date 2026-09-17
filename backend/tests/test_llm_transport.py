import asyncio
from dataclasses import replace
import smtplib
import socket
import ssl
from types import SimpleNamespace

import pytest

from app.services.email import EmailUnavailable, SMTPMailer, smtp_error_code
from app.services.llm import DashScopeLLM, LLMError


def test_async_sdk_receives_timeout_budget_and_explicit_session(app, monkeypatch):
    import aiohttp
    import dashscope
    captured = {}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): captured["closed"] = True
    async def call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200, usage={"input_tokens": 2, "output_tokens": 3}, output=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="回答"), finish_reason="stop")]))
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(dashscope.AioGeneration, "call", call)
    settings = replace(app.state.settings, dashscope_api_key="test-only-key")
    answer = asyncio.run(DashScopeLLM(settings).chat([]))
    assert answer.content == "回答" and answer.input_tokens == 2
    assert captured["request_timeout"] == 90 and captured["max_tokens"] == 2048
    assert isinstance(captured["session"], Session) and captured["closed"]


def test_upstream_error_text_is_not_returned(app, monkeypatch):
    import dashscope
    async def rejected(**kwargs):
        return SimpleNamespace(status_code=403, message="private-upstream-value")
    monkeypatch.setattr(dashscope.AioGeneration, "call", rejected)
    with pytest.raises(LLMError) as error:
        asyncio.run(DashScopeLLM(replace(app.state.settings, dashscope_api_key="test-only-key")).chat([]))
    assert error.value.code == "LLM_AUTH_FAILED"
    assert "private-upstream-value" not in str(error.value)


@pytest.mark.parametrize("error,code", [
    (smtplib.SMTPAuthenticationError(535, b"private"), "SMTP_AUTH_FAILED"),
    (TimeoutError(), "SMTP_TIMEOUT"),
    (socket.gaierror(), "SMTP_DNS_FAILED"),
    (ssl.SSLError(), "SMTP_TLS_FAILED"),
])
def test_smtp_classifies_errors_without_provider_text(error, code):
    assert smtp_error_code(error) == code


def test_placeholder_host_is_rejected_without_connecting(app):
    settings = replace(app.state.settings, smtp_host="smtp.example.com", smtp_username="sender", smtp_password="secret", smtp_from="sender@example.com")
    with pytest.raises(EmailUnavailable) as error:
        SMTPMailer(settings).ensure_ready()
    assert error.value.code == "SMTP_PLACEHOLDER_HOST"


def test_smtp_quit_failure_does_not_invalidate_accepted_mail(app, monkeypatch):
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): raise smtplib.SMTPResponseException(500, b"QUIT failed")
        def login(self, *args): pass
        def send_message(self, message): return {}
    monkeypatch.setattr(smtplib, "SMTP_SSL", Connection)
    settings = replace(app.state.settings, smtp_host="smtp.test.invalid", smtp_username="sender", smtp_password="secret", smtp_from="sender@example.com")
    SMTPMailer(settings).send_code("recipient@example.com", "345678", "register", 300)
