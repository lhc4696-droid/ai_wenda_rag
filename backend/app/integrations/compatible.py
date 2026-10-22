"""OpenAI-compatible chat adapter; endpoint and credentials belong to one account."""

import asyncio
import base64
from contextlib import aclosing

from app.attachments.context import ATTACHMENT_POLICY

from .compatible_transport import post_completion, response_choice, usage_count
from .llm import LLMError, LLMResult


MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _read_image(path):
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise LLMError("历史图片文件暂不可用，请重新上传图片", "LLM_IMAGE_UNAVAILABLE", 409) from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise LLMError("图片过大，无法发送给自定义模型，请缩小后重新上传", "LLM_REQUEST_TOO_LARGE", 413)
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


class OpenAICompatibleLLM:
    billing_source = "custom"

    def __init__(self, settings, profile):
        self._settings = settings
        self._profile = profile
        self.name = profile.chat_model
        # 使用用户自定义的 max_output_tokens，如果没有则使用全局配置
        self._max_output_tokens = profile.max_output_tokens or settings.chat_max_output_tokens

    def model_for(self, messages):
        if any(getattr(message, "images", []) for message in messages):
            if not self._profile.vision_model:
                raise LLMError("当前会话包含图片，请在模型设置中填写支持图片的视觉模型名称", "VISION_NOT_CONFIGURED", 422)
            return self._profile.vision_model
        return self._profile.chat_model

    async def chat(self, messages):
        model = self.model_for(messages)
        payload_messages = [{"role": "system", "content": self._settings.system_prompt + ATTACHMENT_POLICY}]
        for message in messages:
            images = getattr(message, "images", [])
            content = message.content
            if images:
                parts = [{"type": "text", "text": content}]
                for path in images:
                    uri = await asyncio.to_thread(_read_image, path)
                    parts.append({"type": "image_url", "image_url": {"url": uri}})
                content = parts
            payload_messages.append({"role": message.role, "content": content})
        response = await post_completion(
            self._profile.base_url, self._profile.api_key,
            {"model": model, "messages": payload_messages, "stream": False,
             "max_tokens": self._max_output_tokens},
            self._settings.chat_timeout,
            proxy_url=self._settings.custom_model_proxy_url,
            proxy_base_urls=self._settings.custom_model_proxy_base_urls,
        )
        message, reason = response_choice(response)
        content = message.get("content")
        if isinstance(content, list) and all(
                isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                for part in content):
            content = "\n".join(part["text"] for part in content)
        if not isinstance(content, str) or not content.strip():
            raise LLMError("自定义模型未返回有效回答，请确认模型支持聊天接口", "LLM_EMPTY_RESPONSE")
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise LLMError("自定义模型返回的文本编码无效", "LLM_INVALID_RESPONSE") from None
        return LLMResult(content.strip(), usage_count(response, "prompt_tokens"),
                         usage_count(response, "completion_tokens"), reason, model)

    async def stream_chat(self, messages):
        """流式生成响应"""
        model = self.model_for(messages)
        payload_messages = [{"role": "system", "content": self._settings.system_prompt + ATTACHMENT_POLICY}]
        for message in messages:
            images = getattr(message, "images", [])
            content = message.content
            if images:
                parts = [{"type": "text", "text": content}]
                for path in images:
                    uri = await asyncio.to_thread(_read_image, path)
                    parts.append({"type": "image_url", "image_url": {"url": uri}})
                content = parts
            payload_messages.append({"role": message.role, "content": content})

        from .compatible_transport import post_completion_stream
        finished = False
        async with aclosing(post_completion_stream(
            self._profile.base_url, self._profile.api_key,
            {"model": model, "messages": payload_messages, "stream": True,
             "max_tokens": self._max_output_tokens},
            self._settings.chat_timeout,
            proxy_url=self._settings.custom_model_proxy_url,
            proxy_base_urls=self._settings.custom_model_proxy_base_urls,
        )) as stream:
            async for event in stream:
                try:
                    choices = event["choices"]
                    if not isinstance(choices, list):
                        raise ValueError()
                    content, reason = "", None
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content") or ""
                        reason = choice.get("finish_reason") or None
                        if reason == "null":
                            reason = None
                        if not isinstance(content, str) or (reason is not None and not isinstance(reason, str)):
                            raise ValueError()
                        content.encode("utf-8")
                    if reason:
                        finished = True
                    yield LLMResult(content, usage_count(event, "prompt_tokens"),
                                    usage_count(event, "completion_tokens"), reason, model)
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise LLMError("自定义模型返回了无效的流式数据", "LLM_INVALID_RESPONSE") from None
        if not finished:
            raise LLMError("模型响应意外中断，请重试本次问题", "LLM_STREAM_INCOMPLETE")
