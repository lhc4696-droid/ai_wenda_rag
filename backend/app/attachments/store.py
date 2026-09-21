import time
from uuid import uuid4
from app.attachments.schemas import AttachmentError, public_attachment


class AttachmentStore:
    def __init__(self, auth, settings):
        self.auth, self.settings = auth, settings

    @staticmethod
    def owned(db, user_id, identifier):
        row = db.execute("SELECT * FROM attachments WHERE id = ? AND user_id = ? AND status != 'deleting'", (identifier, user_id)).fetchone()
        if row is None or (row["conversation_id"] is None and row["expires_at"] <= int(time.time())):
            raise AttachmentError("附件不存在或无法访问", 404, "ATTACHMENT_NOT_FOUND")
        return dict(row)

    def get(self, user_id, identifier):
        with self.auth.connect() as db:
            return self.owned(db, user_id, identifier)

    def reserve(self, user_id, filename, extension, kind):
        now, identifier = int(time.time()), uuid4().hex
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.auth._reserve_buckets(db, [(f"upload:user:{user_id}", 20, 300)], now)
            used = db.execute("SELECT coalesce(sum(reserved_bytes), 0) FROM attachments WHERE user_id = ?", (user_id,)).fetchone()[0]
            # 预留额外空间给压缩后的图片预览，失败/待清理的文件也占额，直到物理删除。
            reservation = self.settings.attachment_max_bytes + 3 * 1024 * 1024
            if used + reservation > self.settings.attachment_user_quota:
                raise AttachmentError("附件存储额度不足，请删除不需要的会话或附件", 413, "ATTACHMENT_QUOTA")
            db.execute("""INSERT INTO attachments
                (id, user_id, filename, extension, kind, reserved_bytes, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'uploading', ?, ?)""", (identifier, user_id, filename, extension, kind, reservation, now, now + 600))
        return identifier

    def finish(self, user_id, identifier, size, result, disk_size):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.owned(db, user_id, identifier)
            if row["status"] != "uploading":
                raise AttachmentError("附件已取消，请重新上传", 409)
            db.execute("""UPDATE attachments SET status = ?, size = ?, reserved_bytes = ?, media_type = ?,
                extracted_text = ?, warning = ?, error_message = ?, expires_at = ? WHERE id = ?""",
                ("failed" if result.get("error") else "ready", size, disk_size, result.get("media_type", "application/octet-stream"),
                 result.get("text", ""), result.get("warning", ""), result.get("error", ""), int(time.time()) + 86400, identifier))
            return public_attachment(self.owned(db, user_id, identifier))

    def abandon(self, user_id, identifier):
        with self.auth.connect() as db:
            db.execute("UPDATE attachments SET status = 'deleting' WHERE id = ? AND user_id = ? AND conversation_id IS NULL", (identifier, user_id))

    def remove(self, user_id, identifier):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.owned(db, user_id, identifier)
            if row["conversation_id"]:
                raise AttachmentError("已发送的附件随会话保留，请通过删除会话清理", 409, "ATTACHMENT_BOUND")
            if row["status"] == "uploading":
                raise AttachmentError("附件仍在处理中，请稍后再删除", 409)
            db.execute("UPDATE attachments SET status = 'deleting' WHERE id = ?", (identifier,))

    @staticmethod
    def for_message(db, message_id):
        rows = db.execute("""SELECT a.* FROM attachments a JOIN message_attachments ma ON a.id = ma.attachment_id
            WHERE ma.message_id = ? ORDER BY ma.position""", (message_id,)).fetchall()
        return [public_attachment(row) for row in rows]

    @classmethod
    def bind(cls, db, user_id, conversation_id, message_id, identifiers):
        for position, identifier in enumerate(identifiers):
            row = cls.owned(db, user_id, identifier)
            if row["status"] != "ready" or (not row["conversation_id"] and row["expires_at"] <= int(time.time())):
                raise AttachmentError("附件未处理完成、已失败或已过期，请重新上传", 409, "ATTACHMENT_NOT_READY")
            if row["conversation_id"] not in (None, conversation_id):
                raise AttachmentError("附件不能跨会话使用，请在当前会话重新上传", 409, "ATTACHMENT_OTHER_CONVERSATION")
            db.execute("UPDATE attachments SET conversation_id = ?, expires_at = 0 WHERE id = ?", (conversation_id, identifier))
            db.execute("INSERT INTO message_attachments VALUES (?, ?, ?)", (message_id, identifier, position))
