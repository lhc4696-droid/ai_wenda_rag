from typing import Literal
from pydantic import BaseModel


class AttachmentError(Exception):
    def __init__(self, message, status=400, code="ATTACHMENT_INVALID"):
        super().__init__(message)
        self.status, self.code = status, code


class AttachmentResponse(BaseModel):
    id: str
    filename: str
    kind: Literal["image", "document"]
    media_type: str
    size: int
    status: Literal["uploading", "ready", "failed", "deleting"]
    warning: str
    error_message: str
    created_at: int


def public_attachment(row):
    return AttachmentResponse.model_validate(dict(row)).model_dump()
