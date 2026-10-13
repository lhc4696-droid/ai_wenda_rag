"""Local browser tasks have no filesystem endpoints on the server."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth.routes import require_trusted_origin, require_user
from app.auth.schemas import UserResponse
from .local_schemas import (
    LocalCapabilities, LocalCreate, LocalResults, LocalTaskResponse, LocalTaskList,
    LocalConversationCreate, LocalConversationDetail, LocalConversationList, LocalConversationResponse,
    LocalProjectCreate, LocalProjectResponse, LocalProjectList, LocalProjectRename,
)
from .local_service import LOCAL_PROMPT
from .local_store import public_local
from .schemas import CodingError
from .service import database_call


router = APIRouter(prefix="/api/coding/local", tags=["本地文件 Coding Agent"], dependencies=[Depends(require_trusted_origin)])


@router.get("/capabilities", response_model=LocalCapabilities, summary="本地编程模型与轮次限制")
def capabilities(request: Request, user: UserResponse = Depends(require_user)):
    settings = request.app.state.settings
    profile = request.app.state.model_settings.public(user.id)
    model = (profile["coding_model"] or profile["chat_model"]) if profile["enabled"] else settings.qwen_coding_model or settings.qwen_model
    return {"model": model, "max_steps": settings.coding_max_steps}


@router.post("/projects", status_code=201, response_model=LocalProjectResponse,
             summary="创建本地编程项目；目录权限仍仅保存在用户浏览器")
async def create_project(data: LocalProjectCreate, request: Request, user: UserResponse = Depends(require_user)):
    return await database_call(request.app.state.local_coding.store.create_project, user.id, data)


@router.get("/projects", response_model=LocalProjectList)
def projects(request: Request, user: UserResponse = Depends(require_user)):
    return {"items": request.app.state.local_coding.store.list_projects(user.id)}


@router.delete("/projects/{identifier}", status_code=204,
               summary="删除项目及其对话记录；不删除本地文件或浏览器恢复记录")
async def remove_project(identifier: UUID, request: Request, user: UserResponse = Depends(require_user)):
    await database_call(request.app.state.local_coding.store.remove_project, user.id, identifier.hex)
    return Response(status_code=204)


@router.patch("/projects/{identifier}", response_model=LocalProjectResponse,
              summary="修改项目显示名称，不改变目录和授权；留空分配下一个默认名称")
async def rename_project(identifier: UUID, data: LocalProjectRename, request: Request, user: UserResponse = Depends(require_user)):
    return await database_call(request.app.state.local_coding.store.rename_project, user.id, identifier.hex, data)


@router.post("/conversations", status_code=201, response_model=LocalConversationResponse,
             summary="在指定项目中创建独立编程会话；不授予后端磁盘权限")
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
    profile = await database_call(request.app.state.model_settings.resolve, user.id)
    if not profile and not request.app.state.settings.dashscope_api_key:
        raise CodingError("请在账号设置中配置自己的模型，或由维护者配置 DASHSCOPE_API_KEY", "CODING_MODEL_NOT_CONFIGURED", 503)
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
