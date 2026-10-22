"""User-owned Chat Completions model with the existing bounded tool protocol."""

from app.integrations.compatible_transport import post_completion, response_choice, usage_count
from app.integrations.llm import LLMError

from .model import CodingReply
from .schemas import CodingError
from .tools import TOOL_DEFINITIONS


class OpenAICompatibleCodingModel:
    billing_source = "custom"

    def __init__(self, settings, profile, tool_definitions=None):
        self.settings = settings
        self.profile = profile
        self.name = profile.coding_model or profile.chat_model
        self.tool_definitions = TOOL_DEFINITIONS if tool_definitions is None else tool_definitions

    async def call(self, messages):
        try:
            response = await post_completion(
                self.profile.base_url, self.profile.api_key,
                {"model": self.name, "messages": messages, "tools": self.tool_definitions,
                 "tool_choice": "auto", "parallel_tool_calls": False, "stream": False,
                 "max_tokens": 4096},
                self.settings.chat_timeout,
                proxy_url=self.settings.custom_model_proxy_url,
                proxy_base_urls=self.settings.custom_model_proxy_base_urls,
            )
            message, reason = response_choice(response)
        except LLMError as exc:
            code = "CODING_MODEL_TIMEOUT" if exc.code == "LLM_TIMEOUT" else "CODING_" + exc.code
            raise CodingError(str(exc), code, exc.status_code) from None
        if reason == "length":
            raise CodingError("模型输出达到长度上限，请缩小单次修改范围后重试", "CODING_MODEL_TRUNCATED", 503)
        try:
            content = message.get("content")
            content = "" if content is None else content
            calls = message.get("tool_calls")
            calls = [] if calls is None else calls
            if not isinstance(content, str) or len(content) > 40000 or not isinstance(calls, list) or len(calls) > 4:
                raise ValueError()
            content.encode("utf-8")
            normalized, identifiers = [], set()
            for call in calls:
                if not isinstance(call, dict) or call.get("type", "function") != "function":
                    raise ValueError()
                identifier, function = call["id"], call["function"]
                name, arguments = function["name"], function["arguments"]
                if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 200 or identifier in identifiers
                        or not isinstance(name, str) or not 1 <= len(name) <= 100
                        or not isinstance(arguments, str) or len(arguments) > 300000):
                    raise ValueError()
                identifiers.add(identifier)
                identifier.encode("utf-8")
                name.encode("utf-8")
                arguments.encode("utf-8")
                normalized.append({"id": identifier, "type": "function",
                                   "function": {"name": name, "arguments": arguments}})
            if not content.strip() and not normalized:
                raise ValueError()
            return CodingReply(content.strip(), normalized, usage_count(response, "prompt_tokens"),
                               usage_count(response, "completion_tokens"))
        except (KeyError, TypeError, ValueError, IndexError):
            raise CodingError("编程模型返回了无效数据，请换用支持工具调用的兼容模型", "CODING_MODEL_INVALID", 503) from None
