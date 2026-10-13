from contextlib import asynccontextmanager
import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.routes import get_app_settings, require_trusted_origin, require_user, router as auth_router
from app.auth.store import AuthStore
from app.config import Settings, get_settings
from app.schemas import ChatRequest, ChatResponse, ErrorResponse, HealthResponse
from app.integrations.llm import DashScopeLLM, LLMError, LLMProvider
from app.integrations.compatible import OpenAICompatibleLLM
from app.model_settings.store import ModelSettingsStore
from app.model_settings.schemas import ModelSettingsError
from app.model_settings.routes import router as model_settings_router
from app.integrations.email import SMTPMailer
from app.auth.schemas import UserResponse
from app.chat.service import ChatService
from app.chat.store import ModelCallStore, ChatLimit
from app.observability import request_context
from app.schemas import ChatUsageResponse
from app.conversations.routes import create_router as conversation_router
from app.conversations.store import ConversationStore, HistoryError
from app.conversations.service import ConversationService
from app.attachments.service import AttachmentService
from app.attachments.routes import router as attachment_router
from app.attachments.schemas import AttachmentError
from app.rag.service import RagService
from app.coding.schemas import CodingError
from app.coding.store import retire_zip_tasks
from app.coding.local_routes import router as local_coding_router
from app.coding.local_store import LocalCodingStore
from app.coding.local_service import LocalCodingService


def get_llm_provider(request: Request, user: UserResponse = Depends(require_user),
                     settings: Settings = Depends(get_app_settings)) -> LLMProvider:
    # 每次请求独立快照，不能修改共享 Settings 或全局 SDK 的 key/base_url。
    profile = request.app.state.model_settings.resolve(user.id)
    if profile:
        return OpenAICompatibleLLM(settings, profile)
    else:
        # 使用默认的 DashScope LLM
        return DashScopeLLM(settings)


def create_app(config: Settings | None = None) -> FastAPI:
    settings = config or get_settings()
    store = AuthStore(settings.auth_db_path)
    model_settings = ModelSettingsStore(store, settings)
    model_calls = ModelCallStore(store)
    chat_service = ChatService(model_calls, settings)
    attachments = AttachmentService(store, settings, model_settings)
    rag = RagService(store, settings)
    attachments.rag = rag
    local_coding = LocalCodingService(LocalCodingStore(store), model_calls, settings, model_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        # Local MVP is a single-worker service. An interrupted process must not
        # silently resume old code-running tasks after a restart.
        await asyncio.to_thread(retire_zip_tasks, store)
        await asyncio.to_thread(local_coding.store.recover_interrupted)
        cleaner = asyncio.create_task(attachments.cleanup_loop())
        try:
            yield
        finally:
            await local_coding.shutdown()
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)

    application = FastAPI(title="知言 AI API", description="账号、个人模型配置、聊天历史、私有附件、按需 RAG 与本地文件编程。登录使用 HttpOnly Cookie。", version="0.10.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.auth_store = store
    application.state.model_settings = model_settings
    application.state.mailer = SMTPMailer(settings)
    application.state.model_calls = model_calls
    application.state.conversations = ConversationStore(store)
    application.state.attachments = attachments
    application.state.rag = rag
    application.state.local_coding = local_coding
    application.state.conversation_service = ConversationService(application.state.conversations, chat_service, settings, attachments, rag)
    application.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID", "X-Error-Code", "Retry-After"],
    )
    application.include_router(auth_router)
    application.include_router(model_settings_router)
    application.include_router(attachment_router)
    application.include_router(local_coding_router)
    application.include_router(conversation_router(get_llm_provider))

    @application.exception_handler(AttachmentError)
    @application.exception_handler(ModelSettingsError)
    @application.exception_handler(CodingError)
    @application.exception_handler(HistoryError)
    async def history_error(request: Request, exc: HistoryError):
        headers = {"X-Error-Code": exc.code, "Cache-Control": "no-store"}
        if getattr(exc, "retry_after", 0):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)}, headers=headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic 原始错误包含 input；注册/登录失败时不能将密码回显给客户端。
        errors = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors}, headers={"Cache-Control": "no-store"})

    application.middleware("http")(request_context)

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @application.post(
        "/api/chat", response_model=ChatResponse, deprecated=True, summary="兼容旧客户端的临时聊天，不保存历史",
        dependencies=[Depends(require_trusted_origin)],
        responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
    )
    async def chat(request: ChatRequest, http_request: Request, user: UserResponse = Depends(require_user), provider: LLMProvider = Depends(get_llm_provider)) -> ChatResponse:
        try:
            return await chat_service.answer(request.messages, user.id, http_request.state.request_id, provider, http_request.is_disconnected)
        except ChatLimit as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after), "X-Error-Code": exc.code}) from None
        except LLMError as exc:
            headers = {"X-Error-Code": exc.code}
            if exc.retry_after:
                headers["Retry-After"] = str(exc.retry_after)
            raise HTTPException(exc.status_code, str(exc), headers=headers) from None

    @application.get("/api/chat/usage", response_model=ChatUsageResponse, dependencies=[Depends(require_trusted_origin)], summary="当前账号今日调用统计，不包含聊天正文")
    def chat_usage(user: UserResponse = Depends(require_user)):
        return model_calls.usage(user.id, settings)

    return application


app = create_app()
