"""Bounded direct transport and an operator-controlled local proxy route.

The DNS policy is applied by the connector's resolver, not by a separate probe.
That distinction prevents a second DNS lookup from bypassing URL validation.
Only exact operator-configured bases may use proxy-side DNS instead; the trusted
proxy is responsible for destination resolution and network access restrictions.
"""

import asyncio
import codecs
import json
import socket

from .endpoint_policy import _public_ip, normalize_base_url, normalize_proxy_settings
from .llm import LLMError


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
class PublicResolver:
    """aiohttp resolver which rejects every non-public answer before connecting."""

    def __init__(self):
        from aiohttp.resolver import DefaultResolver
        self._resolver = DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records or any(not _public_ip(record.get("host", "")) for record in records):
            raise LLMError(
                "模型地址解析到了禁止访问的网络。若开启 Clash/TUN/Fake-IP，请由部署者配置本机代理及该 Base URL 的代理白名单；不要关闭安全检查",
                "LLM_ENDPOINT_BLOCKED", 422,
            )
        return records

    async def close(self):
        await self._resolver.close()


def usage_count(response: dict, key: str) -> int | None:
    usage = response.get("usage")
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def response_choice(response: dict) -> tuple[dict, str | None]:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError()
        choice = choices[0]
        message = choice["message"]
        reason = choice.get("finish_reason")
        if (not isinstance(message, dict) or message.get("role", "assistant") != "assistant"
                or (reason is not None and (not isinstance(reason, str) or len(reason) > 100))):
            raise ValueError()
        return message, reason
    except (KeyError, TypeError, ValueError):
        raise LLMError("模型服务返回了无法识别的数据，请检查接口兼容性", "LLM_INVALID_RESPONSE") from None


def _reject_status(status: int) -> None:
    if status == 429:
        raise LLMError("自定义模型服务限流或额度不足，请稍后重试", "LLM_RATE_LIMIT", 429, 30)
    if status in (401, 403):
        raise LLMError("自定义模型的 API Key 或权限有误，请检查模型设置", "LLM_AUTH_FAILED")
    if status in (400, 404, 405, 413, 422):
        raise LLMError("自定义模型不支持该地址、模型名或请求参数，请检查模型设置与接口兼容性", "LLM_REQUEST_REJECTED")
    if 300 <= status < 400:
        raise LLMError("模型地址返回了跳转；为保护 API Key，请直接填写最终 HTTPS 基础地址", "LLM_REDIRECT_BLOCKED", 422)
    raise LLMError("自定义模型服务暂时不可用，请稍后重试")


async def post_completion(base_url: str, api_key: str, payload: dict, timeout: float, *,
                          proxy_url: str = "", proxy_base_urls: tuple[str, ...] = ()) -> dict:
    """One request only: no retries, proxy inheritance, redirects, or error-body echo."""
    try:
        normalized_base = normalize_base_url(base_url)
        endpoint = normalized_base + "/chat/completions"
    except ValueError:
        raise LLMError("自定义模型的 Base URL 不符合安全要求，请重新设置", "LLM_ENDPOINT_INVALID", 422) from None
    try:
        configured_proxy, allowed_bases = normalize_proxy_settings(proxy_url, proxy_base_urls)
    except ValueError:
        raise LLMError("后端自定义模型代理配置无效，请联系部署者检查代理地址和 Base URL 白名单", "LLM_PROXY_CONFIG_INVALID", 503) from None
    # Exact base matching includes scheme, host, port and path. No suffix match,
    # wildcard, automatic fallback, or user-controlled proxy settings.
    proxy = configured_proxy if normalized_base in allowed_bases else None
    if (not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096
            or not all(33 <= ord(char) <= 126 for char in api_key)):
        raise LLMError("自定义模型 API Key 格式不正确，请重新设置", "LLM_AUTH_FAILED")
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise LLMError("请求内容格式不正确，请调整消息后重试", "LLM_INVALID_REQUEST", 422) from None
    if len(body) > MAX_REQUEST_BYTES:
        raise LLMError("发送给自定义模型的内容过大，请减少图片或上下文", "LLM_REQUEST_TOO_LARGE", 413)
    try:
        import aiohttp
    except ImportError:
        raise LLMError("模型依赖不完整，请安装后端 requirements.txt", "LLM_DEPENDENCY_MISSING") from None

    resolver = None if proxy else PublicResolver()
    try:
        async with asyncio.timeout(timeout):
            # For the proxy route, CONNECT uses the original hostname and TLS
            # validates that hostname. Only the numeric loopback proxy is dialed
            # locally; target DNS and egress checks belong to the trusted proxy.
            connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, limit=2, ssl=True)
            async with aiohttp.ClientSession(
                    connector=connector, trust_env=False, auto_decompress=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=timeout, connect=min(15, timeout)),
            ) as session:
                async with session.post(
                        endpoint, data=body, allow_redirects=False, proxy=proxy,
                        headers={"Authorization": "Bearer " + api_key,
                                 "Content-Type": "application/json", "Accept": "application/json",
                                 "Accept-Encoding": "identity"},
                ) as response:
                    if response.status != 200:
                        _reject_status(response.status)
                    if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                        raise LLMError("模型服务返回了不支持的压缩响应", "LLM_INVALID_RESPONSE")
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise LLMError("模型返回的数据过大，请减少单次输出", "LLM_RESPONSE_TOO_LARGE")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise LLMError("模型返回的数据过大，请减少单次输出", "LLM_RESPONSE_TOO_LARGE")
                        data.extend(chunk)
                    try:
                        decoded = json.loads(data.decode("utf-8"))
                    except (UnicodeError, ValueError, RecursionError):
                        raise LLMError("模型服务没有返回有效的 JSON 数据，请检查 Base URL", "LLM_INVALID_RESPONSE") from None
                    if not isinstance(decoded, dict):
                        raise LLMError("模型服务返回了无法识别的数据", "LLM_INVALID_RESPONSE")
                    return decoded
    except LLMError:
        raise
    except TimeoutError:
        raise LLMError("自定义模型响应超时，请稍后重试或缩短问题", "LLM_TIMEOUT", 504) from None
    except aiohttp.ClientHttpProxyError:
        raise LLMError("本机代理拒绝建立 HTTPS 隧道，请检查 HTTP/Mixed 端口及代理规则；当前不支持代理认证", "LLM_PROXY_TUNNEL_FAILED") from None
    except aiohttp.ClientProxyConnectionError:
        raise LLMError("无法连接后端配置的本机代理，请确认 Clash Verge 正在运行、HTTP/Mixed 端口正确，并重启后端使配置生效", "LLM_PROXY_CONNECTION_FAILED") from None
    except (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch):
        raise LLMError("模型 HTTPS 证书或 TLS 握手验证失败，请检查服务地址、证书及代理；不要关闭证书验证", "LLM_TLS_FAILED") from None
    except aiohttp.ClientConnectorError as exc:
        if isinstance(exc.os_error, socket.gaierror):
            raise LLMError("模型域名解析失败，请检查 Base URL、DNS 或配置受控代理", "LLM_DNS_FAILED") from None
        raise LLMError("连接自定义模型失败，请检查网络和模型地址", "LLM_CONNECTION_FAILED") from None
    except Exception:
        # Never log exception strings: HTTP errors may contain the URL or headers.
        raise LLMError("连接自定义模型失败，请检查网络、HTTPS 地址及模型设置", "LLM_CONNECTION_FAILED") from None
    finally:
        if resolver is not None:
            await resolver.close()


async def post_completion_stream(base_url: str, api_key: str, payload: dict, timeout: float, *,
                                 proxy_url: str = "", proxy_base_urls: tuple[str, ...] = ()):
    """流式请求：逐块返回 SSE 数据"""
    try:
        normalized_base = normalize_base_url(base_url)
        endpoint = normalized_base + "/chat/completions"
    except ValueError:
        raise LLMError("自定义模型的 Base URL 不符合安全要求，请重新设置", "LLM_ENDPOINT_INVALID", 422) from None
    try:
        configured_proxy, allowed_bases = normalize_proxy_settings(proxy_url, proxy_base_urls)
    except ValueError:
        raise LLMError("后端自定义模型代理配置无效，请联系部署者检查代理地址和 Base URL 白名单", "LLM_PROXY_CONFIG_INVALID", 503) from None

    proxy = configured_proxy if normalized_base in allowed_bases else None
    if (not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096
            or not all(33 <= ord(char) <= 126 for char in api_key)):
        raise LLMError("自定义模型 API Key 格式不正确，请重新设置", "LLM_AUTH_FAILED")
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise LLMError("请求内容格式不正确，请调整消息后重试", "LLM_INVALID_REQUEST", 422) from None
    if len(body) > MAX_REQUEST_BYTES:
        raise LLMError("发送给自定义模型的内容过大，请减少图片或上下文", "LLM_REQUEST_TOO_LARGE", 413)
    try:
        import aiohttp
    except ImportError:
        raise LLMError("模型依赖不完整，请安装后端 requirements.txt", "LLM_DEPENDENCY_MISSING") from None

    resolver = None if proxy else PublicResolver()
    try:
        async with asyncio.timeout(timeout):
            connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, limit=2, ssl=True)
            async with aiohttp.ClientSession(
                    connector=connector, trust_env=False, auto_decompress=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=timeout, connect=min(15, timeout)),
            ) as session:
                async with session.post(
                        endpoint, data=body, allow_redirects=False, proxy=proxy,
                        headers={"Authorization": "Bearer " + api_key,
                                 "Content-Type": "application/json", "Accept": "text/event-stream",
                                 "Accept-Encoding": "identity"},
                ) as response:
                    if response.status != 200:
                        _reject_status(response.status)

                    if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                        raise LLMError("模型服务返回了不支持的压缩响应", "LLM_INVALID_RESPONSE")
                    async for event in completion_events(response.content.iter_chunked(4096)):
                        yield event

    except LLMError:
        raise
    except TimeoutError:
        raise LLMError("自定义模型响应超时，请稍后重试或缩短问题", "LLM_TIMEOUT", 504) from None
    except aiohttp.ClientHttpProxyError:
        raise LLMError("本机代理拒绝建立 HTTPS 隧道，请检查 HTTP/Mixed 端口及代理规则；当前不支持代理认证", "LLM_PROXY_TUNNEL_FAILED") from None
    except aiohttp.ClientProxyConnectionError:
        raise LLMError("无法连接后端配置的本机代理，请确认 Clash Verge 正在运行、HTTP/Mixed 端口正确，并重启后端使配置生效", "LLM_PROXY_CONNECTION_FAILED") from None
    except (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch):
        raise LLMError("模型 HTTPS 证书或 TLS 握手验证失败，请检查服务地址、证书及代理；不要关闭证书验证", "LLM_TLS_FAILED") from None
    except aiohttp.ClientConnectorError as exc:
        if isinstance(exc.os_error, socket.gaierror):
            raise LLMError("模型域名解析失败，请检查 Base URL、DNS 或配置受控代理", "LLM_DNS_FAILED") from None
        raise LLMError("连接自定义模型失败，请检查网络和模型地址", "LLM_CONNECTION_FAILED") from None
    except Exception:
        raise LLMError("连接自定义模型失败，请检查网络、HTTPS 地址及模型设置", "LLM_CONNECTION_FAILED") from None
    finally:
        if resolver is not None:
            await resolver.close()


async def completion_events(chunks):
    """Incremental UTF-8 + SSE framing with a total wire budget and explicit EOF."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, lines, size, finished = "", [], 0, False
    try:
        async for raw in chunks:
            size += len(raw)
            if size > MAX_RESPONSE_BYTES:
                raise LLMError("模型返回的数据过大，请减少单次输出", "LLM_RESPONSE_TOO_LARGE")
            buffer += decoder.decode(raw)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.removesuffix("\r")
                if line.startswith("data:"):
                    lines.append(line[5:].removeprefix(" "))
                elif not line and lines:
                    data, lines = "\n".join(lines), []
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    if not isinstance(event, dict) or "error" in event:
                        raise ValueError()
                    choices = event.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
                        finished = True
                    yield event
        decoder.decode(b"", final=True)
    except (UnicodeError, ValueError, RecursionError):
        raise LLMError("模型服务返回了无效的流式数据，请检查接口兼容性", "LLM_INVALID_RESPONSE") from None
    if not finished or buffer.strip() or lines:
        raise LLMError("模型响应意外中断，请重试本次问题", "LLM_STREAM_INCOMPLETE")
