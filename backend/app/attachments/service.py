import asyncio
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time

from app.attachments.schemas import AttachmentError
from app.attachments.storage import AttachmentStorage
from app.attachments.store import AttachmentStore
from app.observability import logger

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".txt", ".md", ".pdf", ".docx"}


class AttachmentService:
    def __init__(self, auth, settings, model_settings=None):
        self.store = AttachmentStore(auth, settings)
        self.storage = AttachmentStorage(settings.auth_db_path.parent / "attachments")
        self.settings = settings
        self.model_settings = model_settings
        self.slots = asyncio.Semaphore(2)
        self.rag = None

    def capabilities(self, user_id=None):
        profile = self.model_settings.public(user_id) if self.model_settings and user_id is not None else None
        custom = bool(profile and profile["enabled"])
        vision_model = profile["vision_model"] if custom else self.settings.qwen_vision_model
        return {"extensions": sorted(EXTENSIONS), "max_files": 3,
                "max_bytes": self.settings.attachment_max_bytes,
                "images_enabled": bool(vision_model),
                "vision_model": vision_model or None,
                "chat_model": profile["chat_model"] if custom else self.settings.qwen_model,
                "model_source": "custom" if custom else "default",
                "rag_enabled": self.settings.rag_enabled}

    async def process(self, identifier, extension):
        # Windows 兼容的独立解析进程；不继承模型/SMTP/验证码密钥。
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
        def run():
            try:
                # 不依赖 Windows reload 模式事件循环对子进程的支持；超时会杀掉解析进程。
                completed = subprocess.run(
                    [sys.executable, "-I", "-X", "utf8", str(Path(__file__).with_name("parsers.py")), str(self.storage.path(identifier)), extension],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    env=env, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode or len(completed.stdout) > 200_000:
                    return {"error": "文件解析失败，请拆分或重新导出后重试。"}
                return json.loads(completed.stdout)
            except subprocess.TimeoutExpired:
                return {"error": "文件解析超过 30 秒，请拆分或简化后重试。"}
        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # 退出前等待受限进程结束，不在其写预览时释放并发槽或删除文件。
            await asyncio.shield(task)
            raise

    async def upload(self, user_id, upload):
        filename = (upload.filename or "附件").replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(c for c in filename if c.isprintable())[:180]
        extension = Path(filename).suffix.lower()
        if extension not in EXTENSIONS:
            raise AttachmentError("仅支持 JPG、PNG、WebP、TXT、Markdown、PDF 和 DOCX", 415)
        kind = "image" if extension in {".jpg", ".jpeg", ".png", ".webp"} else "document"
        if kind == "image":
            capabilities = await asyncio.to_thread(self.capabilities, user_id)
            if not capabilities["images_enabled"]:
                raise AttachmentError("当前模型未启用图片能力：自定义模型请在账号设置填写视觉模型；默认模型由维护者配置 QWEN_VISION_MODEL", 422, "VISION_NOT_CONFIGURED")
        if not upload.size or upload.size > self.settings.attachment_max_bytes:
            raise AttachmentError("附件不能为空或超过单文件大小限制", 413)
        async with self.slots:
            identifier = await asyncio.to_thread(self.store.reserve, user_id, filename, extension, kind)
            try:
                size = 0
                with self.storage.create(identifier) as target:
                    while chunk := await upload.read(256 * 1024):
                        size += len(chunk)
                        if size > self.settings.attachment_max_bytes:
                            raise AttachmentError("文件超过大小限制", 413)
                        await asyncio.to_thread(target.write, chunk)
                result = await self.process(identifier, extension)
                preview = self.storage.path(identifier, True)
                disk_size = size + (preview.stat().st_size if preview.exists() else 0)
                if disk_size > self.settings.attachment_max_bytes + 3 * 1024 * 1024:
                    raise AttachmentError("处理后的图片过大，请缩小后重试", 413)
                return await asyncio.to_thread(self.store.finish, user_id, identifier, size, result, disk_size)
            except BaseException:
                # 不在取消时撤回可能已经绑定成功的附件；孤立记录由周期清理兜底。
                await asyncio.shield(asyncio.to_thread(self.store.abandon, user_id, identifier))
                raise

    def cleanup(self):
        now = int(time.time())
        with self.store.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE attachments SET status = 'deleting' WHERE conversation_id IS NULL AND expires_at <= ?", (now,))
            identifiers = [(row[0], row[1]) for row in db.execute("SELECT id, user_id FROM attachments WHERE status = 'deleting'")]
        for identifier, owner in identifiers:
            try:
                if self.settings.rag_enabled and self.rag:
                    # 删除向量成功后才删除源文件和元数据；Qdrant 暂时离线则下次清理重试。
                    asyncio.run(self.rag.vectors.delete_attachment(owner, identifier))
                self.storage.delete(identifier)
                with self.store.auth.connect() as db:
                    db.execute("DELETE FROM attachments WHERE id = ? AND status = 'deleting'", (identifier,))
            except Exception:
                logger.warning("attachment_cleanup_failed id=%s", identifier)
        # 账号被删除时 FK 会清理元数据；仅回收本目录内、超过 24 小时的孤立内部文件。
        if self.storage.root.exists():
            with self.store.auth.connect() as db:
                known = {row[0] for row in db.execute("SELECT id FROM attachments")}
            for path in self.storage.root.iterdir():
                if re.fullmatch(r"[a-f0-9]{32}\.(bin|jpg)", path.name) and path.stem not in known:
                    try:
                        if not path.is_symlink() and path.stat().st_mtime < now - 86400:
                            self.storage.delete(path.stem)
                    except (OSError, ValueError):
                        logger.warning("attachment_orphan_cleanup_failed")

    async def cleanup_loop(self):
        while True:
            try:
                await asyncio.to_thread(self.cleanup)
            except Exception as error:
                logger.warning("attachment_cleanup_unavailable category=%s", type(error).__name__)
            await asyncio.sleep(60)
