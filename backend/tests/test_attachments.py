"""附件回归用例；仅临时库/模型替身，本轮未执行。"""
import asyncio
from types import SimpleNamespace
from dataclasses import replace
from uuid import uuid4
import zipfile
import pytest
from fastapi.testclient import TestClient
from app.attachments.parsers import parse, ParseError
from app.attachments.context import ModelMessage
from app.chat.service import bounded_context
from app.main import get_llm_provider
from app.integrations.llm import LLMResult
from app.integrations.llm import DashScopeLLM
from tests.fakes import register_user


def upload(client, filename="notes.txt", body=b"attachment marker 123", media="text/plain"):
    return client.post("/api/attachments", files={"file": (filename, body, media)})


def conversation(client):
    return client.post("/api/conversations", json={}).json()["id"]


def question(files=(), sequence=0):
    return {"content": "请总结附件", "client_message_id": uuid4().hex, "expected_last_sequence": sequence, "attachment_ids": list(files)}


def test_attachment_ownership_origin_and_private_download(logged_in, app):
    first = upload(logged_in).json()
    assert first["status"] == "ready" and "extracted_text" not in first and "user_id" not in first
    response = logged_in.get(f"/api/attachments/{first['id']}/content")
    assert response.status_code == 200 and response.content == b"attachment marker 123"
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert logged_in.post("/api/attachments", headers={"Origin": "https://evil.invalid"}, files={"file": ("a.txt", b"text")}).status_code == 403
    with TestClient(app) as other:
        assert other.get(f"/api/attachments/{first['id']}").status_code == 401
        register_user(other, {"username": "other_owner", "password": "abcd1234"})
        own = conversation(other)
        assert other.get(f"/api/attachments/{first['id']}").status_code == 404
        assert other.get(f"/api/attachments/{first['id']}/content").status_code == 404
        assert other.delete(f"/api/attachments/{first['id']}").status_code == 404
        assert other.post(f"/api/conversations/{own}/messages", json=question([first["id"]])).status_code == 404
        assert other.get(f"/api/conversations/{own}/messages").json()["messages"] == []


def test_documents_context_replay_followup_and_cleanup(logged_in, app):
    seen = []
    class Model:
        async def chat(self, messages):
            seen.append(messages)
            return LLMResult("附件总结", model="test-model")
    app.dependency_overrides[get_llm_provider] = Model
    file = upload(logged_in).json()
    cid = conversation(logged_in)
    payload = question([file["id"]])
    endpoint = f"/api/conversations/{cid}/messages"
    first = logged_in.post(endpoint, json=payload)
    assert first.status_code == 200
    assert first.json()["user_message"]["attachments"][0]["id"] == file["id"]
    assert "attachment marker 123" in seen[0][-1].content
    assert logged_in.post(endpoint, json=payload).json()["replayed"] is True and len(seen) == 1
    assert logged_in.post(endpoint, json={**payload, "attachment_ids": []}).status_code == 409
    assert logged_in.post(endpoint, json=question(sequence=2)).status_code == 200
    assert "attachment marker 123" in seen[-1][0].content
    other_cid = conversation(logged_in)
    assert logged_in.post(f"/api/conversations/{other_cid}/messages", json=question([file["id"]])).status_code == 409
    assert logged_in.delete(f"/api/attachments/{file['id']}").status_code == 409
    assert logged_in.delete(f"/api/conversations/{cid}").status_code == 204
    assert logged_in.get(f"/api/attachments/{file['id']}/content").status_code == 404
    app.state.attachments.cleanup()
    assert not app.state.attachments.storage.path(file["id"]).exists()


def test_upload_validation_expiry_and_capabilities(logged_in, app):
    assert logged_in.get("/api/attachments/capabilities").json()["images_enabled"] is False
    assert upload(logged_in, "bad.exe").status_code == 415
    assert upload(logged_in, "empty.txt", b"").status_code == 413
    assert upload(logged_in, "a.jpg", b"not an image").status_code == 422
    assert upload(logged_in, "long.txt", b"a" * 16001).json()["status"] == "failed"
    assert logged_in.post("/api/attachments", files=[("file", ("a.txt", b"a")), ("file", ("b.txt", b"b"))]).status_code == 400
    assert logged_in.post("/api/attachments", content=b"x", headers={"content-type": "multipart/form-data; boundary=abc", "content-length": "20000000"}).status_code == 413
    item = upload(logged_in).json()
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE attachments SET expires_at = 1 WHERE id = ?", (item["id"],))
    assert logged_in.get(f"/api/attachments/{item['id']}").status_code == 404
    app.state.attachments.cleanup()
    assert not app.state.attachments.storage.path(item["id"]).exists()


def test_parser_image_docx_pdf_limits(tmp_path):
    from PIL import Image
    from pypdf import PdfWriter
    image = tmp_path / "image.bin"
    Image.new("RGB", (20, 20), "white").save(image, format="PNG")
    result = parse(image, ".png")
    assert result["media_type"] == "image/jpeg" and image.with_suffix(".jpg").exists()
    with pytest.raises(ParseError):
        parse(image, ".jpg")
    docx = tmp_path / "text.bin"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>文档正文</w:t></w:r></w:p></w:body></w:document>')
    assert parse(docx, ".docx")["text"] == "文档正文"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY injected SYSTEM "file:///secret">]><x>&injected;</x>')
    with pytest.raises(Exception):
        parse(docx, ".docx")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = tmp_path / "scan.bin"
    writer.write(pdf)
    with pytest.raises(ParseError, match="扫描件"):
        parse(pdf, ".pdf")


def test_attachment_content_counts_toward_context_budget(app):
    settings = replace(app.state.settings, chat_context_chars=20000)
    with pytest.raises(Exception, match="附件超过"):
        bounded_context([ModelMessage("user", "x" * 19999, [])], settings)
    selected, trimmed = bounded_context([ModelMessage("user", "旧图片", ["a", "b"]), ModelMessage("assistant", "答"), ModelMessage("user", "新图片", ["c", "d"])], settings)
    assert trimmed and len(selected) == 1


def test_attachment_quota_includes_preview_reservation(app):
    with pytest.raises(ValueError, match="预览预留空间"):
        replace(app.state.settings, attachment_user_quota=app.state.settings.attachment_max_bytes)


def test_vision_adapter_sends_private_image_bytes_and_reports_model(tmp_path, app, monkeypatch):
    from dashscope import AioMultiModalConversation
    captured = {}
    async def fake(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200, output=SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=[{"text": "图片回答"}]), finish_reason="stop")]), usage={"input_tokens": 100, "output_tokens": 3})
    monkeypatch.setattr(AioMultiModalConversation, "call", staticmethod(fake))
    image = tmp_path / "private.jpg"
    image.write_bytes(b"test-image-bytes")
    settings = replace(app.state.settings, dashscope_api_key="test-only", qwen_vision_model="test-vision")
    result = asyncio.run(DashScopeLLM(settings).chat([ModelMessage("user", "这是什么", [image])]))
    assert result.content == "图片回答" and result.model == "test-vision"
    assert captured["model"] == "test-vision"
    assert captured["messages"][-1]["content"][0]["image"].startswith("data:image/jpeg;base64,")
    assert str(tmp_path) not in str(captured["messages"])
