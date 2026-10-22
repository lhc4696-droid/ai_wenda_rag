"""Cancellation-safe accounting around one accepted external model call."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
import time

from app.observability import logger


@dataclass
class CallResult:
    state: str = "succeeded"
    error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    output_chars: int | None = None
    input_chars: int | None = None


async def _drain(worker):
    """Finish a DB write even if disconnect/shutdown cancels its caller again."""
    while True:
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.done():
                return worker.result()


async def _finish(store, identifier, result, started):
    values = asdict(result)
    state = values.pop("state")
    worker = asyncio.create_task(asyncio.to_thread(
        store.finish, identifier, state, round((time.monotonic() - started) * 1000), **values))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await _drain(worker)
        except Exception as exc:
            logger.error("model_call_finalize_failed request_id=%s category=%s", identifier, type(exc).__name__)
        raise
    except Exception as exc:
        # A persistence outage must not repeat a paid call; its lease still expires.
        logger.error("model_call_finalize_failed request_id=%s category=%s", identifier, type(exc).__name__)
    finally:
        logger.info("model_call_complete request_id=%s state=%s code=%s", identifier, result.state, result.error_code or "OK")


@asynccontextmanager
async def track_call(store, identifier, user_id, input_chars, settings, model, source, lease_seconds=None):
    started = time.monotonic()
    reservation = asyncio.create_task(asyncio.to_thread(
        store.reserve, identifier, user_id, input_chars, settings, model, source, lease_seconds))
    try:
        await asyncio.shield(reservation)
    except asyncio.CancelledError:
        try:
            await _drain(reservation)
        except Exception:
            pass
        else:
            await _finish(store, identifier, CallResult(state="cancelled", error_code="REQUEST_CANCELLED"), started)
        raise
    result = CallResult()
    try:
        yield result
    except asyncio.CancelledError:
        result.state, result.error_code = "cancelled", "REQUEST_CANCELLED"
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", getattr(exc, "status", None))
        result.state = ("cancelled" if getattr(exc, "code", None) == "REQUEST_CANCELLED" else
                        "timed_out" if isinstance(exc, TimeoutError) or status == 504 else "failed")
        result.error_code = getattr(exc, "code", "INTERNAL_ERROR")
        raise
    finally:
        await _finish(store, identifier, result, started)
