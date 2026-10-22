"""User-run offline regressions: no real model, proxy, DNS, keys or sockets."""

import asyncio
from dataclasses import replace
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.config import Settings
from app.config_groups import CustomModelConfig
from app.integrations import compatible, compatible_transport as transport
from app.integrations.endpoint_policy import normalize_proxy_settings
from app.integrations.llm import LLMError
from app.coding import compatible_model
from app.model_settings import connection_test


BASE = "https://models.example.com/v1"
PROXY = "http://127.0.0.1:7897"
KEY = "fake-key-never-send"
OPTIONS = {"proxy_url": PROXY, "proxy_base_urls": (BASE,)}
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]}


def test_proxy_config_normalizes_exact_bases_without_dns():
    assert normalize_proxy_settings("", ()) == ("", ())
    assert normalize_proxy_settings("http://localhost:7897/", ("https://MODELS.example.com:443/v1/", BASE)) == (PROXY, (BASE,))
    assert normalize_proxy_settings("http://[::1]:7897", (BASE,))[0] == "http://[::1]:7897"


@pytest.mark.parametrize("proxy,bases", [
    (PROXY, ()), ("", (BASE,)), (PROXY, (BASE, "")),
    ("socks5://127.0.0.1:7897", (BASE,)), ("https://127.0.0.1:7897", (BASE,)),
    ("http://127.0.0.1", (BASE,)), ("http://127.0.0.1:0", (BASE,)),
    ("http://127.0.0.1:65536", (BASE,)), ("http://127.0.0.1:7897/path", (BASE,)),
    ("http://name:private-secret@127.0.0.1:7897", (BASE,)),
    ("http://127.0.0.1:7897?key=private-secret", (BASE,)),
    ("http://127.0.0.1:7897#", (BASE,)), ("http://127.0.0.1:7897\nsecret", (BASE,)),
    ("http://127.0.0.1:7897\\@evil.com", (BASE,)),
    ("http://10.0.0.1:7897", (BASE,)), ("http://proxy.example.com:7897", (BASE,)),
    ("http://198.18.0.1:7897", (BASE,)), ("http://0.0.0.0:7897", (BASE,)),
    (PROXY, ("https://*.example.com/v1",)), (PROXY, ("https://127.0.0.1/v1",)),
    (PROXY, ("https://198.18.0.1/v1",)), (PROXY, ("http://models.example.com/v1",)),
    (PROXY, (BASE + "/chat/completions",)), (PROXY, (BASE,) * 33),
])
def test_invalid_proxy_configuration_fails_closed_without_echo(proxy, bases):
    with pytest.raises(ValueError) as caught:
        normalize_proxy_settings(proxy, bases)
    assert "private-secret" not in str(caught.value)


def test_settings_validate_and_hide_proxy_configuration():
    configured = Settings(dashscope_api_key="", qwen_model="test", qwen_vision_model="", qwen_coding_model="", system_prompt="test", cors_origins=(),
                          custom_model=CustomModelConfig(proxy_url="http://localhost:7897/", proxy_base_urls=(BASE,)))
    assert configured.custom_model_proxy_url == PROXY
    assert BASE not in repr(configured) and PROXY not in repr(configured)
    with pytest.raises(ValueError):
        replace(configured, custom_model=replace(configured.custom_model, proxy_base_urls=()))


@pytest.fixture
def wire(monkeypatch):
    state = SimpleNamespace(posts=[], connectors=[], sessions=[], status=200, error=None,
                            resolve=AsyncMock(return_value=[{"host": "198.18.0.5"}]))
    monkeypatch.setattr("aiohttp.resolver.DefaultResolver", lambda: SimpleNamespace(
        resolve=state.resolve, close=AsyncMock(),
    ))

    class Connector:
        def __init__(self, **options):
            self.options = options
            state.connectors.append(options)

    class Response:
        headers = {}
        content_length = None

        def __init__(self, connector):
            self.connector = connector
            self.content = self

        @property
        def status(self):
            return state.status

        async def __aenter__(self):
            if self.connector.options["resolver"] is not None:
                await self.connector.options["resolver"].resolve("models.example.com", 443)
            if state.error:
                raise state.error
            return self

        async def __aexit__(self, *args):
            return False

        async def iter_chunked(self, _size):
            for chunk in getattr(state, "chunks", [json.dumps(ANSWER).encode()]):
                yield chunk

    class Session:
        def __init__(self, **options):
            self.connector = options["connector"]
            state.sessions.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **options):
            state.posts.append((url, options))
            return Response(self.connector)

    monkeypatch.setattr(aiohttp, "TCPConnector", Connector)
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    return state


def send(base=BASE, **options):
    return asyncio.run(transport.post_completion(base, KEY, {"model": "test"}, 10, **options))


def test_proxy_skips_target_fake_dns_but_keeps_https_and_no_redirects(wire):
    assert send("https://MODELS.example.com:443/v1/", **OPTIONS) == ANSWER
    wire.resolve.assert_not_awaited()
    assert wire.connectors[0]["resolver"] is None
    assert wire.connectors[0]["ssl"] is True
    assert wire.sessions[0]["trust_env"] is False
    url, options = wire.posts[0]
    assert url == BASE + "/chat/completions"
    assert options["proxy"] == PROXY
    assert options["allow_redirects"] is False
    assert options["headers"]["Authorization"] == "Bearer " + KEY
    assert KEY not in options["data"].decode()


def test_unconfigured_direct_route_still_rejects_fake_ip(wire, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    with pytest.raises(LLMError) as caught:
        send()
    assert caught.value.code == "LLM_ENDPOINT_BLOCKED"
    assert "Clash" in str(caught.value)
    assert wire.posts[0][1]["proxy"] is None
    assert wire.sessions[0]["trust_env"] is False


@pytest.mark.parametrize("base", [
    "https://models.example.com/v2", "https://models.example.com:8443/v1",
    "https://sub.models.example.com/v1", "https://models.example.com.evil.com/v1",
    BASE + "/other", "https://models.example.com/V1",
])
def test_proxy_allowlist_is_not_a_domain_suffix_or_path_prefix(wire, base):
    with pytest.raises(LLMError) as caught:
        send(base, **OPTIONS)
    assert caught.value.code == "LLM_ENDPOINT_BLOCKED"
    assert wire.posts[0][1]["proxy"] is None


def test_unlisted_public_base_remains_direct_and_private_answers_stay_blocked(wire):
    wire.resolve.return_value = [{"host": "8.8.8.8"}]
    assert send("https://other.example.com/v1", **OPTIONS) == ANSWER
    assert wire.posts[0][1]["proxy"] is None
    wire.resolve.return_value = [{"host": "8.8.8.8"}, {"host": "127.0.0.1"}]
    with pytest.raises(LLMError) as caught:
        send("https://other.example.com/v1", **OPTIONS)
    assert caught.value.code == "LLM_ENDPOINT_BLOCKED"


def test_malformed_config_and_private_targets_fail_before_connecting(wire):
    with pytest.raises(LLMError) as caught:
        send(proxy_url=PROXY)
    assert caught.value.code == "LLM_PROXY_CONFIG_INVALID"
    with pytest.raises(LLMError) as caught:
        send("https://127.0.0.1/v1", **OPTIONS)
    assert caught.value.code == "LLM_ENDPOINT_INVALID"
    assert not wire.posts and not wire.connectors


def test_proxy_does_not_follow_redirects_or_retry_direct(wire):
    wire.status = 302
    with pytest.raises(LLMError) as caught:
        send(**OPTIONS)
    assert caught.value.code == "LLM_REDIRECT_BLOCKED"
    assert len(wire.posts) == 1 and wire.posts[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("kind,code", [
    ("connection", "LLM_PROXY_CONNECTION_FAILED"),
    ("tunnel", "LLM_PROXY_TUNNEL_FAILED"),
    ("tls", "LLM_TLS_FAILED"), ("dns", "LLM_DNS_FAILED"), ("timeout", "LLM_TIMEOUT"),
])
def test_network_failures_are_classified_without_secrets_or_retry(wire, kind, code):
    secret = "private-upstream-secret"
    if kind == "connection":
        wire.error = aiohttp.ClientProxyConnectionError(None, OSError(10061, secret))
    elif kind == "tunnel":
        wire.error = aiohttp.ClientHttpProxyError(None, (), status=407, message=secret)
    elif kind == "tls":
        wire.error = aiohttp.ClientSSLError(None, OSError(secret))
    elif kind == "dns":
        wire.error = aiohttp.ClientConnectorError(None, socket.gaierror(-1, secret))
    else:
        wire.error = TimeoutError(secret)
    with pytest.raises(LLMError) as caught:
        send(**OPTIONS)
    assert caught.value.code == code
    assert secret not in str(caught.value) and KEY not in str(caught.value)
    assert len(wire.posts) == 1


def test_chat_coding_and_probe_share_operator_options(monkeypatch):
    settings = Settings(dashscope_api_key="", qwen_model="test", qwen_vision_model="", qwen_coding_model="", system_prompt="test", cors_origins=(),
                        custom_model=CustomModelConfig(proxy_url=PROXY, proxy_base_urls=(BASE,)))
    profile = SimpleNamespace(base_url=BASE, api_key=KEY, chat_model="test", coding_model="test", vision_model="", max_output_tokens=None)
    sender = AsyncMock(return_value=ANSWER)
    monkeypatch.setattr(compatible, "post_completion", sender)
    monkeypatch.setattr(compatible_model, "post_completion", sender)
    monkeypatch.setattr(connection_test, "post_completion", sender)
    asyncio.run(compatible.OpenAICompatibleLLM(settings, profile).chat([]))
    asyncio.run(compatible_model.OpenAICompatibleCodingModel(settings, profile, []).call([]))
    asyncio.run(connection_test._ProbeProvider(profile, {}, 10, settings).chat(()))
    assert sender.await_count == 3
    assert all(call.kwargs == OPTIONS for call in sender.call_args_list)
