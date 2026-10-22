"""Encrypted per-account credentials and atomic configuration changes.

Only a resolved backend profile contains plaintext credentials. Public metadata,
SQLite rows, exception messages and repr() must not expose them.
"""

import json
import time

from app.integrations.compatible_transport import normalize_base_url
from .schemas import ModelConnectionTestRequest, ModelProfile, ModelSettingsError, ModelSettingsUpdate


class ModelSettingsStore:
    def __init__(self, auth_store, settings):
        self.auth_store = auth_store
        self.settings = settings
        self._cipher = None
        if settings.model_config_secret:
            try:
                from cryptography.fernet import Fernet

                self._cipher = Fernet(settings.model_config_secret.encode("ascii"))
            except (ImportError, ValueError, UnicodeError):
                # Invalid deployment configuration only disables this optional feature.
                # Do not echo the secret or silently send custom requests to defaults.
                pass

    def _require_storage(self):
        if self._cipher is None:
            raise ModelSettingsError(
                "自定义模型的安全存储尚未配置，请联系管理员安装依赖并配置 MODEL_CONFIG_SECRET",
                status=503, code="MODEL_STORAGE_UNAVAILABLE",
            )

    def _public(self, row):
        return {
            "enabled": bool(row["enabled"]) if row else False,
            "base_url": row["base_url"] if row else "",
            "chat_model": row["chat_model"] if row else "",
            "coding_model": row["coding_model"] if row else "",
            "vision_model": row["vision_model"] if row else "",
            "max_output_tokens": row["max_output_tokens"] if row and row["max_output_tokens"] else None,
            "has_api_key": bool(row and row["api_key_encrypted"]),
            "storage_ready": self._cipher is not None,
            "default_chat_model": self.settings.qwen_model,
            "default_coding_model": self.settings.qwen_coding_model or self.settings.qwen_model,
            "default_vision_model": self.settings.qwen_vision_model,
            "default_max_output_tokens": self.settings.chat_max_output_tokens,
            "updated_at": row["updated_at"] if row else None,
        }

    def public(self, user_id):
        with self.auth_store.connect() as db:
            row = db.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (user_id,)).fetchone()
        return self._public(row)

    def _encrypt(self, user_id, base_url, key):
        self._require_storage()
        plaintext = json.dumps({"owner": user_id, "base_url": base_url, "key": key}, separators=(",", ":"))
        return self._cipher.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def _decrypt(self, user_id, base_url, ciphertext):
        self._require_storage()
        try:
            value = json.loads(self._cipher.decrypt(ciphertext.encode("ascii")))
            if not isinstance(value, dict) or value.get("owner") != user_id or value.get("base_url") != base_url:
                raise ValueError("owner or destination mismatch")
            key = value.get("key")
            if not isinstance(key, str) or not key or len(key) > 2048 or any(not 33 <= ord(c) <= 126 for c in key):
                raise ValueError("invalid key")
            return key
        except Exception:
            raise ModelSettingsError(
                "已保存的 API Key 无法解密，请重新填写并保存；若更换过服务器加密密钥，请联系管理员",
                status=503, code="MODEL_KEY_UNREADABLE",
            ) from None

    def resolve(self, user_id):
        with self.auth_store.connect() as db:
            row = db.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (user_id,)).fetchone()
        if not row or not row["enabled"]:
            return None
        try:
            base_url = normalize_base_url(row["base_url"])
        except ValueError:
            raise ModelSettingsError("已保存的服务地址无效，请重新配置自定义模型", code="MODEL_BASE_URL_INVALID") from None
        if not row["chat_model"]:
            raise ModelSettingsError("请先配置自定义聊天模型", code="MODEL_NAME_REQUIRED")
        key = self._decrypt(user_id, base_url, row["api_key_encrypted"])
        return ModelProfile(
            base_url=base_url, api_key=key, chat_model=row["chat_model"],
            coding_model=row["coding_model"] or row["chat_model"], vision_model=row["vision_model"],
            max_output_tokens=row["max_output_tokens"] if row["max_output_tokens"] else None,
        )

    def preview(self, user_id, data):
        """Resolve a one-request draft without saving, enabling or changing it."""
        if not isinstance(data, ModelConnectionTestRequest):
            data = ModelConnectionTestRequest.model_validate(data)
        try:
            base_url = normalize_base_url(data.base_url)
        except ValueError as exc:
            raise ModelSettingsError(str(exc), code="MODEL_BASE_URL_INVALID") from None
        key = data.api_key.get_secret_value() if data.api_key is not None else ""
        if not key:
            with self.auth_store.connect() as db:
                previous = db.execute(
                    "SELECT base_url, api_key_encrypted FROM user_model_settings WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            if not previous or not previous["api_key_encrypted"]:
                raise ModelSettingsError("请填写 API Key 后再测试连接", code="MODEL_KEY_REQUIRED")
            # Compare the stored destination before decryption, never send a saved
            # credential to a different endpoint merely to test a form draft.
            if previous["base_url"] != base_url:
                raise ModelSettingsError(
                    "测试新的 Base URL 时必须重新填写 API Key，不能沿用原服务的密钥",
                    code="MODEL_KEY_REQUIRED_FOR_URL_CHANGE",
                )
            key = self._decrypt(user_id, base_url, previous["api_key_encrypted"])
        return ModelProfile(
            base_url=base_url, api_key=key, chat_model=data.chat_model,
            coding_model=data.coding_model or data.chat_model,
        )

    @staticmethod
    def _require_idle(db, user_id):
        now = int(time.time())
        coding = db.execute(
            "SELECT 1 FROM local_coding_tasks WHERE user_id = ? AND status IN ('queued','running','waiting_tools') AND deadline_at > ? LIMIT 1",
            (user_id, now),
        ).fetchone()
        model_call = db.execute(
            "SELECT 1 FROM model_calls WHERE user_id = ? AND state = 'running' AND lease_expires_at > ? LIMIT 1",
            (user_id, now),
        ).fetchone()
        if coding or model_call:
            raise ModelSettingsError(
                "当前还有聊天或编程任务正在执行，请等待结束或先停止任务，再修改模型配置",
                status=409, code="MODEL_SETTINGS_BUSY",
            )

    def update(self, user_id, data):
        if not isinstance(data, ModelSettingsUpdate):
            data = ModelSettingsUpdate.model_validate(data)
        changes = data.model_dump(exclude_unset=True)
        key_value = changes.pop("api_key", None)
        key = key_value.get_secret_value() if key_value is not None else ""
        if "base_url" in changes and changes["base_url"]:
            try:
                changes["base_url"] = normalize_base_url(changes["base_url"])
            except ValueError as exc:
                raise ModelSettingsError(str(exc), code="MODEL_BASE_URL_INVALID") from None
        with self.auth_store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_idle(db, user_id)
            previous = db.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (user_id,)).fetchone()
            values = {
                "enabled": bool(previous["enabled"]) if previous else False,
                "base_url": previous["base_url"] if previous else "",
                "chat_model": previous["chat_model"] if previous else "",
                "coding_model": previous["coding_model"] if previous else "",
                "vision_model": previous["vision_model"] if previous else "",
                "max_output_tokens": previous["max_output_tokens"] if previous else None,
                "api_key_encrypted": previous["api_key_encrypted"] if previous else "",
            }
            values.update(changes)
            if previous and previous["api_key_encrypted"] and values["base_url"] != previous["base_url"] and not key:
                raise ModelSettingsError(
                    "更换 Base URL 时必须重新填写 API Key，防止把原服务的密钥发送给其他服务",
                    code="MODEL_KEY_REQUIRED_FOR_URL_CHANGE",
                )
            if key:
                values["api_key_encrypted"] = self._encrypt(user_id, values["base_url"], key)
            if values["enabled"]:
                self._require_storage()
                if not values["base_url"] or not values["chat_model"] or not values["api_key_encrypted"]:
                    raise ModelSettingsError("启用自定义模型需要 Base URL、API Key 和聊天模型名称", code="MODEL_CONFIGURATION_REQUIRED")
                # A changed deployment secret or cross-owner copied ciphertext cannot
                # be re-enabled merely by updating a non-secret field.
                self._decrypt(user_id, values["base_url"], values["api_key_encrypted"])
            db.execute(
                """INSERT INTO user_model_settings
                   (user_id,enabled,base_url,api_key_encrypted,chat_model,coding_model,vision_model,max_output_tokens,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   enabled=excluded.enabled,base_url=excluded.base_url,api_key_encrypted=excluded.api_key_encrypted,
                   chat_model=excluded.chat_model,coding_model=excluded.coding_model,
                   vision_model=excluded.vision_model,max_output_tokens=excluded.max_output_tokens,updated_at=excluded.updated_at""",
                (user_id, int(values["enabled"]), values["base_url"], values["api_key_encrypted"],
                 values["chat_model"], values["coding_model"], values["vision_model"], values["max_output_tokens"], int(time.time())),
            )
            row = db.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (user_id,)).fetchone()
        return self._public(row)

    def delete(self, user_id):
        with self.auth_store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_idle(db, user_id)
            db.execute("DELETE FROM user_model_settings WHERE user_id = ?", (user_id,))
        return self._public(None)
