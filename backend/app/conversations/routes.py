from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.routes import require_trusted_origin, require_user
from app.auth.schemas import UserResponse
from app.conversations.schemas import CreateConversation, RenameConversation, SendMessage, ConversationResponse, ConversationPage, MessagePage, TurnResponse


def create_router(provider_dependency):
    router = APIRouter(prefix="/api/conversations", tags=["聊天历史"], dependencies=[Depends(require_trusted_origin)])

    @router.post("", response_model=ConversationResponse, status_code=201, summary="新建会话，同一 ID 可安全重试")
    def create(data: CreateConversation, request: Request, user: UserResponse = Depends(require_user)):
        return request.app.state.conversations.create(user.id, data.id.hex)

    @router.get("", response_model=ConversationPage, summary="分页查询自己的历史会话")
    def list_conversations(request: Request, user: UserResponse = Depends(require_user), limit: int = Query(20, ge=1, le=50), cursor: str | None = Query(None, pattern=r"^\d{1,13}:[a-f0-9]{32}$")):
        return request.app.state.conversations.list(user.id, limit, cursor)

    @router.get("/{conversation_id}/messages", response_model=MessagePage, summary="读取消息，默认最近 50 条，before 向前翻页")
    def read(conversation_id: UUID, request: Request, user: UserResponse = Depends(require_user), limit: int = Query(50, ge=1, le=100), before: int | None = Query(None, ge=1, le=2_147_483_647)):
        return request.app.state.conversations.read(user.id, conversation_id.hex, limit, before)

    @router.patch("/{conversation_id}", response_model=ConversationResponse, summary="重命名自己的会话")
    def rename(conversation_id: UUID, data: RenameConversation, request: Request, user: UserResponse = Depends(require_user)):
        return request.app.state.conversations.rename(user.id, conversation_id.hex, data.title)

    @router.delete("/{conversation_id}", status_code=204, summary="删除会话及其全部消息")
    def delete(conversation_id: UUID, request: Request, user: UserResponse = Depends(require_user)):
        request.app.state.conversations.delete(user.id, conversation_id.hex)
        return Response(status_code=204)

    @router.post("/{conversation_id}/messages", response_model=TurnResponse, summary="保存问题并生成回答；重复编号不重复保存已完成消息")
    async def send(conversation_id: UUID, data: SendMessage, request: Request, user: UserResponse = Depends(require_user), provider=Depends(provider_dependency)):
        return await request.app.state.conversation_service.send(user.id, conversation_id.hex, data, request.state.request_id, provider, request.is_disconnected)

    return router
