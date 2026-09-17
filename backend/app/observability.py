"""请求关联编号与脱敏日志。禁止记录请求正文、Cookie 和密钥。"""

from contextvars import ContextVar
import logging
import time
import uuid

from fastapi.responses import JSONResponse


request_id: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("uvicorn.error")


async def request_context(request, call_next):
    identifier = uuid.uuid4().hex
    token = request_id.set(identifier)
    request.state.request_id = identifier
    started = time.monotonic()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("request_failed request_id=%s category=%s", identifier, type(exc).__name__)
            response = JSONResponse(status_code=500, content={"detail": "服务暂时无法处理请求，请稍后重试"}, headers={"X-Error-Code": "INTERNAL_ERROR"})
        response.headers["X-Request-ID"] = identifier
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        # 使用已匹配的路由模板，不记录用户拼接的 URL、查询参数或正文。
        route = request.scope.get("route")
        logger.info("request_complete request_id=%s route=%s status=%s duration_ms=%s", identifier, getattr(route, "path", "unmatched"), response.status_code, round((time.monotonic() - started) * 1000))
        return response
    finally:
        request_id.reset(token)
