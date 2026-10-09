"""Local browser tasks have no filesystem endpoints on the server."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth.routes import require_trusted_origin, require_user
from app.auth.schemas import UserResponse
from .local_schemas import (
    LocalCapabilities, LocalCreate, LocalResults, LocalTaskResponse, LocalTaskList,
    LocalConversationCreate, LocalConversationDetail, LocalConversationList, LocalConversationResponse,
)
from .local_service import LOCAL_PROMPT
from .local_store import public_local
from .schemas import CodingError
from .service import database_call


router = APIRouter(prefix="/api/coding/local", tags=["本地文件 Coding Agent"], dependencies=[Depends(require_trusted_origin)])


@router.get("/capabilities", response_model=LocalCapabilities, summary="本地编程模型与轮次限制")
def capabilities(request: Request, user: UserResponse = Depends(require_user)):
    settings = request.app.state.settings
    return {"model": settings.qwen_coding_model or settings.qwen_model, "max_steps": settings.coding_max_steps}


@router.post("/conversations", status_code=201, response_model=LocalConversationResponse,
             summary="创建绑定一个本地目录的编程会话；不授予后端磁盘权限")
async def create_conversation(data: LocalConversationCreate, request: Request, user: UserResponse = Depends(require_user)):
    return await database_call(request.app.state.local_coding.store.create_conversation, user.id, data)


@router.get("/conversations", response_model=LocalConversationList)
def conversations(request: Request, user: UserResponse = Depends(require_user)):
    return {"items": request.app.state.local_coding.store.list_conversations(user.id)}


@router.get("/conversations/{identifier}", response_model=LocalConversationDetail)
def conversation(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    return request.app.state.local_coding.store.get_conversation(user.id, identifier.hex)


@router.delete("/conversations/{identifier}", status_code=204, summary="删除编程会话记录，不删除用户原文件或浏览器备份")
async def remove_conversation(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    await database_call(request.app.state.local_coding.store.remove_conversation, user.id, identifier.hex)
    return Response(status_code=204)


async def accept_and_launch(request, function, *args):
    service = request.app.state.local_coding
    acceptance = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        task = await asyncio.shield(acceptance)
    except asyncio.CancelledError:
        task = await acceptance
        if task["status"] == "queued":
            service.launch(task["user_id"], task["id"])
        raise
    if task["status"] == "queued":
        service.launch(task["user_id"], task["id"])
    return public_local(task)


@router.post("/tasks", status_code=202, response_model=LocalTaskResponse, summary="在已绑定的编程会话中启动新一轮任务，不上传整个目录")
async def create(data: LocalCreate, request: Request, user: UserResponse = Depends(require_user)):
    if not request.app.state.settings.dashscope_api_key:
        raise CodingError("请先配置 DASHSCOPE_API_KEY", "CODING_MODEL_NOT_CONFIGURED", 503)
    return await accept_and_launch(request, request.app.state.local_coding.store.create,
                                   user.id, data, request.app.state.settings, LOCAL_PROMPT)


@router.get("/tasks", response_model=LocalTaskList)
def tasks(request: Request, user: UserResponse = Depends(require_user)):
    return {"items": request.app.state.local_coding.store.list(user.id)}


@router.get("/tasks/{identifier}", response_model=LocalTaskResponse)
def task(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    return public_local(request.app.state.local_coding.store.get(user.id, identifier.hex))


@router.post("/tasks/{identifier}/results", status_code=202, response_model=LocalTaskResponse)
async def results(identifier: UUID, data: LocalResults, request: Request, user: UserResponse = Depends(require_user)):
    return await accept_and_launch(request, request.app.state.local_coding.store.results, user.id, identifier.hex, data)


@router.post("/tasks/{identifier}/cancel", response_model=LocalTaskResponse)
async def cancel(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    return public_local(await request.app.state.local_coding.cancel(user.id, identifier.hex))


@router.delete("/tasks/{identifier}", status_code=204)
async def remove(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    await database_call(request.app.state.local_coding.store.remove, user.id, identifier.hex)
    return Response(status_code=204)
