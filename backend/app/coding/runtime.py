"""Shared coding workers and metered model calls, independent of execution tools."""

from abc import ABC, abstractmethod
import asyncio
import json
import logging
from uuid import uuid4

from app.chat.accounting import track_call
from app.chat.store import ChatLimit
from .schemas import CodingError


logger = logging.getLogger(__name__)


async def database_call(function, *args, **kwargs):
    """Wait for a started atomic DB write when its caller is cancelled."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(worker)
        except Exception:
            pass
        raise


class CodingRuntime(ABC):
    """Concrete services provide their model and one task-turn implementation."""

    def __init__(self, store, model_calls, settings):
        self.store, self.model_calls, self.settings = store, model_calls, settings
        self.tasks, self.started = {}, set()
        self.closing = False

    def launch(self, user_id, task_id):
        if task_id not in self.tasks:
            worker = asyncio.create_task(self._run(user_id, task_id), name=f"coding-{task_id}")
            self.tasks[task_id] = worker
            worker.add_done_callback(lambda done: self._done(task_id, done))

    def _done(self, task_id, worker):
        if self.tasks.get(task_id) is worker:
            self.tasks.pop(task_id, None)
            self.started.discard(task_id)
        if not worker.cancelled() and worker.exception() is not None:
            # Do not log source code, provider responses or credentials.
            logger.error("Coding task persistence failed task=%s category=%s", task_id, type(worker.exception()).__name__)

    async def shutdown(self):
        self.closing = True
        running = list(self.tasks.items())
        for identifier, worker in running:
            if identifier in self.started and not worker.cancelling():
                worker.cancel()
        await asyncio.gather(*(worker for _, worker in running), return_exceptions=True)

    async def _model_call(self, user_id, messages, model=None):
        # A per-call provider keeps different accounts' keys and endpoints isolated.
        model = model if model is not None else self.model
        input_size = len(json.dumps(messages, ensure_ascii=False))
        if input_size > 80000:
            raise CodingError("任务上下文达到上限，修改已保留；请缩小范围或从当前结果继续新任务", "CODING_CONTEXT_LIMIT")
        identifier = uuid4().hex
        while True:
            try:
                async with track_call(self.model_calls, identifier, user_id, input_size, self.settings,
                                      model.name, getattr(model, "billing_source", "default")) as recorded:
                    reply = await model.call(messages)
                    recorded.output_chars = len(reply.content)
                    recorded.input_tokens, recorded.output_tokens = reply.input_tokens, reply.output_tokens
                    return reply
            except ChatLimit as exc:
                if exc.code in {"CHAT_RATE_LIMIT", "CHAT_USER_BUSY", "CHAT_SERVER_BUSY"}:
                    await asyncio.sleep(min(exc.retry_after, 60))
                    continue
                raise CodingError(str(exc), exc.code, 429) from None

    @abstractmethod
    async def _run(self, user_id, task_id):
        """Run one concrete task turn."""
