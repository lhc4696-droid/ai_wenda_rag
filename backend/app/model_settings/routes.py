import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.routes import require_trusted_origin, require_user
from app.auth.schemas import UserResponse
from app.chat.store import ChatLimit
from .connection_test import run_connection_test
from .schemas import ModelConnectionTestRequest, ModelConnectionTestResponse, ModelSettingsResponse, ModelSettingsUpdate


router = APIRouter(
    prefix="/api/model-settings", tags=["个人模型配置"],
    dependencies=[Depends(require_trusted_origin)],
)


@router.get("", response_model=ModelSettingsResponse, summary="读取当前账号模型配置；不返回 API Key")
def read_settings(request: Request, response: Response, user: UserResponse = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.model_settings.public(user.id)


@router.patch("", response_model=ModelSettingsResponse, summary="更新当前账号的自定义模型；密钥加密保存")
def update_settings(data: ModelSettingsUpdate, request: Request, response: Response,
                    user: UserResponse = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.model_settings.update(user.id, data)


@router.delete("", response_model=ModelSettingsResponse, summary="移除当前账号的自定义模型配置并恢复系统默认")
def delete_settings(request: Request, response: Response, user: UserResponse = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.model_settings.delete(user.id)


@router.post("/test", response_model=ModelConnectionTestResponse,
             summary="测试当前表单的聊天或 Coding 兼容性；不保存配置，不读取会话或文件")
async def test_connection(data: ModelConnectionTestRequest, request: Request, response: Response,
                          user: UserResponse = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    profile = await asyncio.to_thread(request.app.state.model_settings.preview, user.id, data)
    try:
        return await run_connection_test(
            request.app.state.model_calls, request.app.state.settings, profile, data.target,
            user.id, request.state.request_id, request.is_disconnected,
        )
    except ChatLimit as exc:
        raise HTTPException(429, str(exc), headers={
            "Retry-After": str(exc.retry_after), "X-Error-Code": exc.code, "Cache-Control": "no-store",
        }) from None
