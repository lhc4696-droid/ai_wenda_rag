import asyncio
from contextlib import aclosing

from app.chat.accounting import track_call
from app.chat.store import ModelCallStore
from app.config import Settings
from app.observability import logger
from app.schemas import ChatMessage, ChatResponse
from app.integrations.llm import LLMError, LLMProvider, LLMResult
from app.attachments.context import ATTACHMENT_POLICY


# 上下文预算常量
IMAGE_CHAR_BUDGET = 4000  # 每张图片预留的字符预算（保守估计）
MAX_IMAGES_IN_CONTEXT = 3  # 上下文中允许的最大图片数量
MAX_ANSWER_LENGTH = 20_000  # 回答内容的最大字符数
DISCONNECT_CHECK_INTERVAL = 0.25  # 检查客户端断连的间隔（秒）
CLEANUP_TIMEOUT = 1  # 任务清理的超时时间（秒）


def message_cost(message):
    # 图片按保守的额外输入预算计算，不把 Base64 长度误当成文本 Token。
    return len(message.content) + IMAGE_CHAR_BUDGET * len(getattr(message, "images", []))


def bounded_context(messages: list[ChatMessage], settings: Settings):
    selected = list(messages)
    budget = settings.chat_context_chars - len(settings.system_prompt) - len(ATTACHMENT_POLICY)
    if message_cost(selected[-1]) > budget:
        raise LLMError("问题与附件超过本次上下文预算，请减少附件或缩短问题后重试", "CHAT_INPUT_TOO_LONG", 422)
    total = sum(message_cost(item) for item in selected)
    def too_many_images():
        return sum(len(getattr(item, "images", [])) for item in selected) > MAX_IMAGES_IN_CONTEXT
    while (total > budget or too_many_images()) and len(selected) > 1:
        total -= message_cost(selected.pop(0))
        # 不让裁剪后的上下文以孤立的 assistant 回答开头。
        while len(selected) > 1 and selected[0].role == "assistant":
            total -= message_cost(selected.pop(0))
    return selected, len(selected) != len(messages)


class ChatService:
    def __init__(self, store: ModelCallStore, settings: Settings):
        self.store, self.settings = store, settings

    @staticmethod
    async def generate(provider, messages, disconnected):
        if disconnected is None:
            return await provider.chat(messages)

        async def watch_disconnect():
            while not await disconnected():
                await asyncio.sleep(DISCONNECT_CHECK_INTERVAL)

        model_task = asyncio.create_task(provider.chat(messages))
        disconnect_task = asyncio.create_task(watch_disconnect())
        try:
            done, _ = await asyncio.wait((model_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED)
            if model_task in done:
                return await model_task
            raise LLMError("客户端已结束等待", "REQUEST_CANCELLED", 499)
        finally:
            model_task.cancel()
            disconnect_task.cancel()
            # 某些 ASGI receive 实现会在 is_disconnected 中持续等待；不能因此把
            # 已完成的模型响应一直拖到 CHAT_TIMEOUT。取消后限时收尾。
            _, pending = await asyncio.wait((model_task, disconnect_task), timeout=CLEANUP_TIMEOUT)
            if pending:
                logger.warning("chat_disconnect_watch_cleanup_pending count=%s", len(pending))

    @staticmethod
    async def generate_stream(provider, messages, on_delta):
        """A stream item contains a text delta and optional final usage metadata."""
        content, final = "", LLMResult("")
        async with aclosing(provider.stream_chat(messages)) as stream:
            async for item in stream:
                if not isinstance(item, LLMResult) or not isinstance(item.content, str):
                    raise LLMError("模型服务返回了无效的流式数据", "LLM_INVALID_RESPONSE")
                if final.finish_reason and item.content:
                    raise LLMError("模型完成后返回了额外内容", "LLM_INVALID_RESPONSE")
                for field in ("input_tokens", "output_tokens", "finish_reason", "model"):
                    value = getattr(item, field)
                    if value is not None:
                        setattr(final, field, value)
                remaining = MAX_ANSWER_LENGTH - len(content)
                delta = item.content[:remaining]
                content += delta
                if delta:
                    await on_delta(delta)
                if len(item.content) > remaining:
                    final.finish_reason = "length"
                    break
        if not final.finish_reason:
            raise LLMError("模型响应意外中断，请重试本次问题", "LLM_STREAM_INCOMPLETE")
        if final.finish_reason not in {"stop", "length"}:
            raise LLMError("模型未能完成本次回答，请调整问题后重试", "LLM_INVALID_RESPONSE")
        final.content = content
        return final

    async def answer(self, messages, user_id: int, identifier: str, provider: LLMProvider, disconnected=None, on_delta=None, prepare=None):
        selected, trimmed = bounded_context(messages, self.settings)
        planned_model = self.settings.qwen_vision_model if any(getattr(item, "images", []) for item in selected) else self.settings.qwen_model
        if callable(getattr(provider, "model_for", None)):
            planned_model = provider.model_for(selected)
        lease = self.settings.chat_timeout + (120 if prepare else 30)
        input_chars = lambda context: sum(len(item.content) for item in context) + len(self.settings.system_prompt) + len(ATTACHMENT_POLICY)
        async with track_call(self.store, identifier, user_id, input_chars(selected), self.settings,
                              planned_model, getattr(provider, "billing_source", "default"), lease) as recorded:
            try:
                if prepare is not None:
                    # The parent allowance and slot cover retrieval plus generation.
                    await prepare()
                    selected, prepared_trimmed = bounded_context(messages, self.settings)
                    trimmed = trimmed or prepared_trimmed
                recorded.input_chars = input_chars(selected)
                async with asyncio.timeout(self.settings.chat_timeout):
                    if on_delta is None:
                        answer = await self.generate(provider, selected, disconnected)
                    else:
                        answer = await self.generate_stream(provider, selected, on_delta)
                result = LLMResult(answer) if isinstance(answer, str) else answer
                if not isinstance(result, LLMResult) or not isinstance(result.content, str) or not result.content.strip():
                    raise LLMError("AI 服务未返回有效回答", "LLM_INVALID_RESPONSE")
                content = result.content.strip()
                truncated = len(content) > MAX_ANSWER_LENGTH or result.finish_reason == "length"
                content = content[:MAX_ANSWER_LENGTH]
                recorded.output_chars = len(content)
                recorded.input_tokens, recorded.output_tokens = result.input_tokens, result.output_tokens
                return ChatResponse(content=content, model=result.model or planned_model, context_trimmed=trimmed,
                                    answer_truncated=truncated, request_id=identifier)
            except TimeoutError:
                raise LLMError("AI 服务响应超时，请稍后重试或缩短问题", "LLM_TIMEOUT", 504) from None
