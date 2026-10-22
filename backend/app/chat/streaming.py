"""SSE presentation of the authoritative conversation workflow."""

import asyncio
import json

import anyio
from fastapi.responses import StreamingResponse

from app.conversations.schemas import TurnResponse
from app.conversations.store import HistoryError
from app.observability import logger


def encode_event(event):
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


async def conversation_events(service, user_id, conversation_id, data, identifier, provider):
    # Bounded buffering applies backpressure instead of accumulating an entire answer.
    queue = asyncio.Queue(maxsize=16)

    async def on_delta(delta):
        await queue.put({"type": "content", "delta": delta})

    async def run():
        try:
            result = await service.send(user_id, conversation_id, data, identifier, provider, on_delta=on_delta)
            # This event is only published AFTER the history transaction commits.
            result = TurnResponse.model_validate(result).model_dump(mode="json")
            await queue.put({"type": "done", "result": result})
        except HistoryError as exc:
            await queue.put({"type": "error", "message": str(exc), "error_code": exc.code,
                             "status": exc.status, "retry_after": exc.retry_after, "request_id": identifier})
        except Exception as exc:
            logger.error("stream_failed request_id=%s category=%s", identifier, type(exc).__name__)
            await queue.put({"type": "error", "message": "本次回答未能确认保存，请刷新会话后再试。",
                             "error_code": "INTERNAL_ERROR", "status": 503,
                             "retry_after": 0, "request_id": identifier})

    worker = asyncio.create_task(run())
    try:
        yield encode_event({"type": "start", "conversation_id": conversation_id, "request_id": identifier})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield encode_event(event)
            if event["type"] in {"done", "error"}:
                break
    finally:
        # Starlette cancels the body iterator on disconnect. Drain DB finalization
        # under a shield so pending messages and running quota slots are released.
        with anyio.CancelScope(shield=True):
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


def conversation_stream_response(service, user_id, conversation_id, data, identifier, provider):
    return StreamingResponse(
        conversation_events(service, user_id, conversation_id, data, identifier, provider),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
