"""DashScope native Function Calling adapter, independent of chat and RAG."""

import asyncio
from dataclasses import dataclass

from .schemas import CodingError
from .tools import TOOL_DEFINITIONS


@dataclass
class CodingReply:
    content: str
    tool_calls: list
    input_tokens: int | None = None
    output_tokens: int | None = None


class CodingModel:
    def __init__(self, settings, tool_definitions=None):
        self.settings = settings
        self.name = settings.qwen_coding_model or settings.qwen_model
        self.tool_definitions = TOOL_DEFINITIONS if tool_definitions is None else tool_definitions

    async def call(self, messages):
        if not self.settings.dashscope_api_key:
            raise CodingError("尚未配置 DASHSCOPE_API_KEY", "CODING_MODEL_NOT_CONFIGURED", 503)
        try:
            import aiohttp
            from dashscope import AioGeneration

            async with aiohttp.ClientSession(trust_env=True) as session:
                async with asyncio.timeout(self.settings.chat_timeout):
                    response = await AioGeneration.call(
                        api_key=self.settings.dashscope_api_key, model=self.name,
                        messages=messages, tools=self.tool_definitions, tool_choice="auto",
                        parallel_tool_calls=False, enable_thinking=False,
                        result_format="message", max_tokens=4096,
                        request_timeout=self.settings.chat_timeout, session=session,
                    )
        except TimeoutError:
            raise CodingError("编程模型响应超时，已保留任务修改", "CODING_MODEL_TIMEOUT", 504) from None
        except ImportError:
            raise CodingError("模型依赖不完整，请安装后端 requirements.txt", "CODING_MODEL_DEPENDENCY", 503) from None
        except Exception:
            raise CodingError("连接编程模型失败，请检查网络及模型配置", "CODING_MODEL_CONNECTION", 503) from None
        if response.status_code != 200:
            if response.status_code in (401, 403):
                message = "模型 API Key 或权限有误，请检查后端配置"
            elif response.status_code == 429:
                message = "模型服务限流或额度不足，请稍后再试"
            else:
                message = "模型调用被拒绝，请确认 QWEN_CODING_MODEL 支持 DashScope 文本 Function Calling"
            raise CodingError(message, "CODING_MODEL_REJECTED", 503)
        try:
            choice = response.output.choices[0]
            message = choice.message
            content = message.get("content") or ""
            calls = message.get("tool_calls") or []
            if not isinstance(content, str) or len(content) > 40000 or not isinstance(calls, list) or len(calls) > 4:
                raise ValueError()
            content.encode("utf-8")
            normalized, ids = [], set()
            for call in calls:
                identifier, function = call["id"], call["function"]
                name, arguments = function["name"], function["arguments"]
                if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 200 or identifier in ids
                        or not isinstance(name, str) or len(name) > 100
                        or not isinstance(arguments, str) or len(arguments) > 300000):
                    raise ValueError()
                ids.add(identifier)
                identifier.encode("utf-8")
                name.encode("utf-8")
                arguments.encode("utf-8")
                normalized.append({"id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}})
            if not content.strip() and not normalized:
                raise ValueError()
            if choice.get("finish_reason") == "length":
                raise CodingError("模型输出达到长度上限，请缩小单次修改范围后重试", "CODING_MODEL_TRUNCATED", 503)
            usage = response.get("usage") or {}
            def count(key):
                value = usage.get(key)
                return value if type(value) is int and value >= 0 else None
            return CodingReply(content.strip(), normalized, count("input_tokens"), count("output_tokens"))
        except (KeyError, AttributeError, TypeError, ValueError, IndexError):
            raise CodingError("编程模型返回了无效数据，请换用支持工具调用的模型", "CODING_MODEL_INVALID", 503) from None
