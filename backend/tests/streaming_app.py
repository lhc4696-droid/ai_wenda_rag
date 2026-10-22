"""Real local HTTP for Playwright; isolated test data and an offline model only."""

import asyncio
import os
from pathlib import Path
import tempfile

from app.config import Settings
from app.main import create_app, get_llm_provider
from app.auth.security import hash_password
from app.integrations.llm import LLMResult, LLMError


root = Path(os.environ["ZHIYAN_STREAM_TEST_ROOT"]).resolve()
if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.name.startswith("zhiyan-stream-test-"):
    raise RuntimeError("Streaming browser tests require a dedicated temporary directory")

app = create_app(Settings(
    dashscope_api_key="", qwen_model="offline-stream", qwen_vision_model="", qwen_coding_model="",
    system_prompt="offline test", cors_origins=("http://127.0.0.1:15173",),
    auth_db_path=root / "auth.sqlite3",
))
app.state.auth_store.initialize()
for username in ("stream_desktop", "stream_mobile"):
    app.state.auth_store.create_user(username, hash_password("test-stream-password"))


class OfflineModel:
    async def stream_chat(self, messages):
        yield LLMResult("第一段：你好🙂。\n\n")
        await asyncio.sleep(5 if "后台" in messages[-1].content else 1.5)
        if "失败" in messages[-1].content:
            raise LLMError("模拟模型中断，请重试。", "LLM_CONNECTION_FAILED")
        if "停止" in messages[-1].content:
            await asyncio.sleep(30)
        yield LLMResult("**第二段：流式回答完成。**", 10, 8, "stop", "offline-stream")


app.dependency_overrides[get_llm_provider] = OfflineModel
