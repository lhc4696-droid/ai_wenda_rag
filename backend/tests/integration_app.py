"""仅供真实 HTTP 测试：邮件写入测试临时目录，不发送、不提供取码 HTTP 接口。"""

import json
import os
from pathlib import Path
import tempfile

from app.config import Settings
from app.config_groups import EmailCodeConfig
from app.main import create_app
from tests.fakes import FakeMailer


root = Path(os.environ["ZHIYAN_TEST_ROOT"]).resolve()
if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.name.startswith("zhiyan-auth-test-"):
    raise RuntimeError("集成测试必须使用独立的临时目录")


class RecordingMailer(FakeMailer):
    def send_code(self, email, code, purpose, ttl):
        super().send_code(email, code, purpose, ttl)
        temporary = root / "outbox.pending"
        temporary.write_text(json.dumps(self.codes), encoding="utf-8")
        temporary.replace(root / "outbox.json")


app = create_app(Settings(dashscope_api_key="", qwen_model="test-model", qwen_vision_model="", qwen_coding_model="", system_prompt="test", cors_origins=tuple(os.environ["CORS_ORIGINS"].split(",")), auth_db_path=root / "auth.sqlite3", email_code=EmailCodeConfig(secret="integration-test-only-secret-never-use-in-production")))
app.state.mailer = RecordingMailer()
