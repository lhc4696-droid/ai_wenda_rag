import asyncio
import time

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

    async def answer(self, messages, user_id: int, identifier: str, provider: LLMProvider, disconnected=None):
        selected, trimmed = bounded_context(messages, self.settings)
        started = time.monotonic()
        planned_model = self.settings.qwen_vision_model if any(getattr(item, "images", []) for item in selected) else self.settings.qwen_model
        if callable(getattr(provider, "model_for", None)):
            planned_model = provider.model_for(selected)
        reservation = asyncio.create_task(asyncio.to_thread(self.store.reserve, identifier, user_id, sum(len(item.content) for item in selected) + len(self.settings.system_prompt) + len(ATTACHMENT_POLICY), self.settings, planned_model))
        try:
            await asyncio.shield(reservation)
        except asyncio.CancelledError:
            # 数据库占额不能因 HTTP 取消而留下一条未释放的运行记录。
            try:
                await asyncio.shield(reservation)
            except Exception:
                pass
            else:
                await asyncio.shield(asyncio.to_thread(self.store.finish, identifier, "cancelled", 0, error_code="REQUEST_CANCELLED"))
            raise
        state, error_code = "failed", "INTERNAL_ERROR"
        result = None
        content = None
        try:
            async with asyncio.timeout(self.settings.chat_timeout):
                answer = await self.generate(provider, selected, disconnected)
            result = LLMResult(answer) if isinstance(answer, str) else answer
            if not isinstance(result, LLMResult) or not isinstance(result.content, str) or not result.content.strip():
                raise LLMError("AI 服务未返回有效回答", "LLM_INVALID_RESPONSE")
            content = result.content.strip()
            truncated = len(content) > MAX_ANSWER_LENGTH or result.finish_reason == "length"
            content = content[:MAX_ANSWER_LENGTH]
            state, error_code = "succeeded", None
            return ChatResponse(content=content, model=result.model or planned_model, context_trimmed=trimmed, answer_truncated=truncated, request_id=identifier)
        except TimeoutError:
            state, error_code = "timed_out", "LLM_TIMEOUT"
            raise LLMError("AI 服务响应超时，请稍后重试或缩短问题", error_code, 504) from None
        except LLMError as exc:
            state = "cancelled" if exc.code == "REQUEST_CANCELLED" else "timed_out" if exc.status_code == 504 else "failed"
            error_code = exc.code
            raise
        except asyncio.CancelledError:
            state, error_code = "cancelled", "REQUEST_CANCELLED"
            raise
        finally:
            duration = round((time.monotonic() - started) * 1000)
            try:
                await asyncio.shield(asyncio.to_thread(self.store.finish, identifier, state, duration,
                    output_chars=len(content) if content is not None else None,
                    input_tokens=result.input_tokens if isinstance(result, LLMResult) else None,
                    output_tokens=result.output_tokens if isinstance(result, LLMResult) else None,
                    error_code=error_code))
            except Exception as exc:
                # 统计写入失败也不能把已经收到的正常回答转换成重试，租约会自动过期。
                logger.error("chat_finalize_failed request_id=%s category=%s", identifier, type(exc).__name__)
            logger.info("chat_complete request_id=%s state=%s duration_ms=%s code=%s", identifier, state, duration, error_code or "OK")
