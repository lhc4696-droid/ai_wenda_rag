import asyncio

from app.chat.store import ChatLimit
from app.conversations.store import HistoryError
from app.observability import logger
from app.integrations.llm import LLMError
from app.attachments.context import prepare_messages
from app.attachments.schemas import AttachmentError


class ConversationService:
    def __init__(self, store, chat_service, settings, attachments=None):
        self.store, self.chat, self.settings = store, chat_service, settings
        self.attachments = attachments

    async def fail(self, user_id, conversation_id, identifier, code, message):
        try:
            await asyncio.shield(asyncio.to_thread(self.store.fail, user_id, conversation_id, identifier, code, message))
        except Exception as exc:
            # 写入失败时由租约恢复；不在日志中写用户问题和原始服务异常。
            logger.error("history_finalize_failed request_id=%s category=%s", identifier, type(exc).__name__)

    async def send(self, user_id, conversation_id, data, identifier, provider, disconnected=None):
        reservation = asyncio.create_task(asyncio.to_thread(self.store.begin, user_id, conversation_id, data, identifier, self.settings.chat_timeout))
        try:
            turn = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(reservation)
            except Exception:
                pass
            await self.fail(user_id, conversation_id, identifier, "REQUEST_CANCELLED", "本次等待已结束，可以重新尝试。")
            raise
        if "cached" in turn:
            return turn["cached"]
        try:
            if self.attachments:
                turn["messages"] = await asyncio.to_thread(prepare_messages, self.store.auth, self.attachments.storage, user_id, turn["records"])
            answer = await self.chat.answer(turn["messages"], user_id, identifier, provider, disconnected)
            answer.context_trimmed = answer.context_trimmed or turn["trimmed"]
            # 完整回复与用户消息完成状态在同一事务提交；响应丢失可用同一编号重放。
            completion = asyncio.create_task(asyncio.to_thread(self.store.complete, user_id, conversation_id, turn["message_id"], identifier, answer))
            try:
                return await asyncio.shield(completion)
            except asyncio.CancelledError:
                # 若已开始提交，先确认事务结束，避免失败标记抢在成功提交前写入。
                try:
                    await asyncio.shield(completion)
                except Exception:
                    pass
                raise
        except asyncio.CancelledError:
            await self.fail(user_id, conversation_id, identifier, "REQUEST_CANCELLED", "本次等待已结束，可以重新尝试。")
            raise
        except AttachmentError as exc:
            await self.fail(user_id, conversation_id, identifier, exc.code, str(exc))
            raise
        except (ChatLimit, LLMError) as exc:
            await self.fail(user_id, conversation_id, identifier, exc.code, str(exc))
            raise HistoryError(str(exc), exc.code, 429 if isinstance(exc, ChatLimit) else exc.status_code, exc.retry_after) from None
        except HistoryError:
            await self.fail(user_id, conversation_id, identifier, "TURN_INTERRUPTED", "本次回答未完成，请刷新后重新尝试。")
            raise
        except Exception as exc:
            await self.fail(user_id, conversation_id, identifier, "HISTORY_SAVE_FAILED", "本次回答未能确认保存，请刷新会话后再试。")
            logger.error("history_send_failed request_id=%s category=%s", identifier, type(exc).__name__)
            raise HistoryError("本次回答未能确认保存，请刷新会话后再试", "HISTORY_SAVE_FAILED", 503) from None
