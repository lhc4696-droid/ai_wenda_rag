from uuid import UUID
import asyncio
from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import UploadFile
from starlette.responses import FileResponse
from starlette.exceptions import HTTPException
from app.auth.routes import require_trusted_origin, require_user
from app.auth.schemas import UserResponse
from app.auth.store import TooManyAttempts
from app.attachments.schemas import AttachmentError, AttachmentResponse, public_attachment

router = APIRouter(prefix="/api/attachments", tags=["聊天附件"], dependencies=[Depends(require_trusted_origin)])


@router.get("/capabilities", summary="附件类型、大小限制和图片能力")
def capabilities(request: Request, user: UserResponse = Depends(require_user)):
    return request.app.state.attachments.capabilities(user.id)


@router.post("", response_model=AttachmentResponse, status_code=201, summary="上传并解析一个私有附件",
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                 "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}}
             }}}}})
async def upload(request: Request, user: UserResponse = Depends(require_user)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise AttachmentError("请使用 multipart/form-data 上传文件", 415)
    service = request.app.state.attachments
    length = request.headers.get("content-length")
    if length and length.isdecimal() and int(length) > service.settings.attachment_max_bytes + 64 * 1024:
        raise AttachmentError("上传请求超过大小限制", 413)
    # 在 multipart 解析前按实际接收字节限流，不能仅依靠可伪造的 Content-Length。
    received = 0
    receive = request._receive
    async def limited_receive():
        nonlocal received
        try:
            event = await asyncio.wait_for(receive(), 20)
        except TimeoutError:
            from starlette.formparsers import MultiPartException
            raise MultiPartException("上传数据等待超时，请检查网络后重试") from None
        received += len(event.get("body", b""))
        if received > service.settings.attachment_max_bytes + 64 * 1024:
            raise HTTPException(413, "上传请求超过大小限制")
        return event
    request._receive = limited_receive
    try:
        async with request.form(max_files=1, max_fields=0, max_part_size=1024) as form:
            upload = form.get("file")
            if len(form) != 1 or not isinstance(upload, UploadFile):
                raise AttachmentError("每次请求只允许一个 file 字段")
            return await service.upload(user.id, upload)
    except TooManyAttempts as error:
        raise HTTPException(429, "附件上传过于频繁，请稍后再试", headers={"Retry-After": str(error.retry_after)}) from None


@router.get("/{attachment_id}", response_model=AttachmentResponse)
def metadata(attachment_id: UUID, request: Request, user: UserResponse = Depends(require_user)):
    return public_attachment(request.app.state.attachments.store.get(user.id, attachment_id.hex))


@router.get("/{attachment_id}/content", summary="读取自己的图片预览或下载文档")
def content(attachment_id: UUID, request: Request, user: UserResponse = Depends(require_user)):
    service = request.app.state.attachments
    row = service.store.get(user.id, attachment_id.hex)
    if row["status"] != "ready":
        raise AttachmentError("附件尚不可读取", 409)
    is_image = row["kind"] == "image"
    path = service.storage.path(row["id"], is_image)
    if not path.is_file():
        raise AttachmentError("附件文件已不可用，请重新上传", 404)
    return FileResponse(path, media_type="image/jpeg" if is_image else "application/octet-stream",
                        filename="image.jpg" if is_image else row["filename"],
                        content_disposition_type="inline" if is_image else "attachment",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.delete("/{attachment_id}", status_code=204, summary="删除未发送的附件，已发送附件随会话清理")
async def remove(attachment_id: UUID, request: Request, user: UserResponse = Depends(require_user)):
    service = request.app.state.attachments
    await asyncio.to_thread(service.store.remove, user.id, attachment_id.hex)
    # 记录先变为不可访问，物理清理失败时后台会重试。
    await asyncio.to_thread(service.cleanup)
    return Response(status_code=204)
