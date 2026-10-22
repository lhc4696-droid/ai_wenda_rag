import asyncio
import base64
from contextlib import aclosing
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol

from app.config import Settings
from app.observability import logger, request_id
from app.schemas import ChatMessage
from app.attachments.context import ATTACHMENT_POLICY
from app.integrations.dashscope_messages import multimodal_messages


class LLMError(RuntimeError):
    """可安全返回的信息，不透传供应商错误正文。"""
    def __init__(self, message: str, code="LLM_UNAVAILABLE", status_code=503, retry_after=0):
        super().__init__(message)
        self.code, self.status_code, self.retry_after = code, status_code, retry_after


@dataclass
class LLMResult:
    # chat(): complete text; stream_chat(): text delta with optional final metadata.
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    model: str | None = None


class LLMProvider(Protocol):
    async def chat(self, messages: list[ChatMessage]) -> LLMResult | str: ...
    async def stream_chat(self, messages: list[ChatMessage]):
        """Yield LLMResult deltas, ending with a finish_reason and optional usage."""
        ...


class DashScopeLLM:
    billing_source = "default"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._max_output_tokens = settings.chat_max_output_tokens

    def model_for(self, messages):
        return self._settings.qwen_vision_model if any(getattr(item, "images", []) for item in messages) else self._settings.qwen_model

    async def chat(self, messages: list[ChatMessage]) -> LLMResult:
        if not self._settings.dashscope_api_key:
            raise LLMError("后端尚未配置 DASHSCOPE_API_KEY", "LLM_NOT_CONFIGURED")
        try:
            from dashscope import AioMultiModalConversation
            import aiohttp
        except ImportError:
            raise LLMError("模型依赖不完整，请安装后端 requirements.txt 中的版本", "LLM_DEPENDENCY_MISSING") from None

        vision = any(getattr(message, "images", []) for message in messages)
        model = self.model_for(messages)
        if vision and not model:
            raise LLMError("当前会话包含图片，但未配置 QWEN_VISION_MODEL", "VISION_NOT_CONFIGURED", 422)
        system = self._settings.system_prompt + ATTACHMENT_POLICY
        payload = [{"role": "system", "content": [{"text": system}] if vision else system}]
        for message in messages:
            content = message.content
            if vision:
                parts = []
                for path in getattr(message, "images", []):
                    raw = await asyncio.to_thread(path.read_bytes)
                    parts.append({"image": "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")})
                content = [*parts, {"text": content}]
            payload.append({"role": message.role, "content": content})

        try:
            # 独立异步连接在取消/超时时关闭，不遗留同步请求线程。
            async with aiohttp.ClientSession(trust_env=True) as session:
                response = await AioMultiModalConversation.call(
                    api_key=self._settings.dashscope_api_key,
                    model=model,
                    messages=multimodal_messages(payload),
                    enable_thinking=False,
                    result_format="message",
                    max_tokens=self._max_output_tokens,
                    request_timeout=self._settings.chat_timeout,
                    session=session,
                )
        except asyncio.TimeoutError:
            raise LLMError("AI 服务响应超时，请稍后重试或缩短问题", "LLM_TIMEOUT", 504) from None
        except aiohttp.ClientConnectorError as exc:
            logger.warning("llm_connection_failed request_id=%s type=connector_error details=%s", request_id.get(), str(exc))
            raise LLMError("无法连接到 AI 服务，请检查网络或稍后重试", "LLM_CONNECTION_FAILED") from None
        except aiohttp.ClientError as exc:
            logger.warning("llm_client_error request_id=%s type=%s details=%s", request_id.get(), type(exc).__name__, str(exc))
            raise LLMError("AI 服务请求失败，请稍后重试", "LLM_CLIENT_ERROR") from None
        except Exception as exc:
            logger.error("llm_unexpected_error request_id=%s type=%s", request_id.get(), type(exc).__name__, exc_info=True)
            raise LLMError("AI 服务遇到意外错误，请稍后重试", "LLM_UNEXPECTED_ERROR") from None

        if response.status_code != HTTPStatus.OK:
            logger.warning("llm_rejected request_id=%s upstream_status=%s", request_id.get(), response.status_code)
            if response.status_code == 429:
                raise LLMError("模型服务暂时繁忙或额度受限，请稍后重试", "LLM_RATE_LIMIT", 429, 30)
            if response.status_code in (401, 403):
                raise LLMError("模型服务认证或权限配置有误，请联系维护者", "LLM_AUTH_FAILED")
            if response.status_code in (400, 404):
                raise LLMError("模型或请求参数不被服务支持，请检查模型配置或缩短上下文", "LLM_REQUEST_REJECTED")
            raise LLMError("模型服务暂时不可用，请稍后重试")

        try:
            choice = response.output.choices[0]
            content = choice.message.content
            if isinstance(content, list):
                content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
            reason = getattr(choice, "finish_reason", None)
        except (AttributeError, IndexError, KeyError, TypeError):
            raise LLMError("AI 服务返回了无法识别的数据", "LLM_INVALID_RESPONSE") from None

        if not isinstance(content, str) or not content.strip():
            raise LLMError("AI 服务未返回有效回答", "LLM_EMPTY_RESPONSE")

        usage = getattr(response, "usage", None)
        def count(name):
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            return value if type(value) is int and value >= 0 else None
        return LLMResult(content.strip(), count("input_tokens"), count("output_tokens"), reason, model)

    async def stream_chat(self, messages: list[ChatMessage]):
        """Explicit incremental SDK output; never silently swallow an upstream error."""
        if not self._settings.dashscope_api_key:
            raise LLMError("后端尚未配置 DASHSCOPE_API_KEY", "LLM_NOT_CONFIGURED")
        try:
            from dashscope import AioMultiModalConversation
            import aiohttp
        except ImportError:
            raise LLMError("模型依赖不完整，请安装后端 requirements.txt", "LLM_DEPENDENCY_MISSING") from None

        model = self.model_for(messages)
        if not model:
            raise LLMError("当前会话未配置可用模型", "VISION_NOT_CONFIGURED", 422)
        payload = [{"role": "system", "content": self._settings.system_prompt + ATTACHMENT_POLICY}]
        for message in messages:
            images = getattr(message, "images", [])
            content = message.content
            if images:
                parts = []
                for path in images:
                    raw = await asyncio.to_thread(path.read_bytes)
                    parts.append({"image": "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")})
                content = [*parts, {"text": content}]
            payload.append({"role": message.role, "content": content})
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                responses = await AioMultiModalConversation.call(
                    api_key=self._settings.dashscope_api_key, model=model,
                    messages=multimodal_messages(payload), enable_thinking=False,
                    result_format="message", max_tokens=self._max_output_tokens,
                    request_timeout=self._settings.chat_timeout, session=session,
                    stream=True, incremental_output=True,
                )
                async with aclosing(responses):
                    async for response in responses:
                        if response.status_code != HTTPStatus.OK:
                            status = response.status_code
                            if status == 429:
                                raise LLMError("模型服务暂时繁忙或额度受限，请稍后重试", "LLM_RATE_LIMIT", 429, 30)
                            if status in (401, 403):
                                raise LLMError("模型服务认证或权限配置有误，请联系维护者", "LLM_AUTH_FAILED")
                            raise LLMError("模型服务未能完成本次回答，请检查模型配置", "LLM_REQUEST_REJECTED")
                        try:
                            choice = response.output.choices[0]
                            content = choice.message.content
                            if isinstance(content, list):
                                if any(not isinstance(part, dict) or not isinstance(part.get("text"), str) for part in content):
                                    raise ValueError()
                                content = "\n".join(part["text"] for part in content)
                            if content is None:
                                content = ""
                            if not isinstance(content, str):
                                raise ValueError()
                            reason = getattr(choice, "finish_reason", None) or None
                            # DashScope uses the string "null" for intermediate chunks.
                            if reason == "null":
                                reason = None
                            if reason is not None and (not isinstance(reason, str) or len(reason) > 100):
                                raise ValueError()
                            content.encode("utf-8")
                        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                            raise LLMError("AI 服务返回了无效的流式数据", "LLM_INVALID_RESPONSE") from None
                        usage = getattr(response, "usage", None)
                        def count(key):
                            value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
                            return value if type(value) is int and 0 <= value <= 1_000_000_000 else None
                        yield LLMResult(content, count("input_tokens"), count("output_tokens"), reason, model)
        except LLMError:
            raise
        except TimeoutError:
            raise LLMError("AI 服务响应超时，请稍后重试", "LLM_TIMEOUT", 504) from None
        except Exception as exc:
            logger.warning("llm_stream_failed request_id=%s category=%s", request_id.get(), type(exc).__name__)
            raise LLMError("AI 服务连接中断，请稍后重试", "LLM_CONNECTION_FAILED") from None
