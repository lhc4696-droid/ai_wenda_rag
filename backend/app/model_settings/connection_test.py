"""One bounded, user-triggered probe; never stores credentials or reads chat data."""

import asyncio
import json
import time

from app.chat.accounting import track_call
from app.chat.service import ChatService
from app.integrations.compatible_transport import post_completion, response_choice, usage_count
from app.integrations.llm import LLMError
from app.observability import logger

from .schemas import ModelConnectionTestResponse


CHAT_PROMPT = "This is a connection test. Reply with the single word OK."
PROBE_SYSTEM = "You are handling a short API compatibility test. Follow the test instruction only."
CODING_PROMPT = (
    "This is a tool compatibility test, not a real task. Call connection_probe "
    "exactly once with the argument ok set to true. Do not write an explanation."
)
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "connection_probe",
        "description": "Report successful tool-call formatting only; this tool is never executed.",
        "parameters": {
            "type": "object", "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"], "additionalProperties": False,
        },
    },
}


def _probe_payload(profile, target):
    model = profile.coding_model if target == "coding" else profile.chat_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROBE_SYSTEM},
            {"role": "user", "content": CODING_PROMPT if target == "coding" else CHAT_PROMPT},
        ],
        "stream": False, "max_tokens": 128,
    }
    if target == "coding":
        payload.update(tools=[PROBE_TOOL], tool_choice="auto", parallel_tool_calls=False)
    return payload


def _validate_probe(response, target):
    message, reason = response_choice(response)
    if reason == "length":
        raise LLMError("小测试输出达到长度上限，暂时无法确认兼容性；这不代表 API Key 一定无效，推理模型也可能先用完测试预算", "MODEL_TEST_TRUNCATED")
    if reason in {"content_filter", "refusal"} or message.get("refusal"):
        raise LLMError("模型拒绝了测试请求，尚未确认该配置可用", "MODEL_TEST_REFUSED")
    if target == "chat":
        content = message.get("content")
        if isinstance(content, list) and all(
                isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                for part in content):
            content = "\n".join(part["text"] for part in content)
        if not isinstance(content, str) or not content.strip() or message.get("tool_calls"):
            raise LLMError("接口已响应，但未返回有效的聊天文本，请确认模型支持 Chat Completions", "MODEL_TEST_CHAT_INVALID")
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise LLMError("模型返回的文本编码无效，尚未确认兼容性", "LLM_INVALID_RESPONSE") from None
        return len(content)

    calls = message.get("tool_calls")
    try:
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError()
        call = calls[0]
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError()
        identifier, function = call.get("id"), call.get("function")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200:
            raise ValueError()
        identifier.encode("utf-8")
        if not isinstance(function, dict) or function.get("name") != "connection_probe":
            raise ValueError()
        arguments = function.get("arguments")
        if not isinstance(arguments, str) or len(arguments) > 1024:
            raise ValueError()
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict) or set(parsed) != {"ok"} or parsed["ok"] is not True:
            raise ValueError()
        content = message.get("content")
        if content is not None and (not isinstance(content, str) or len(content) > 40000):
            raise ValueError()
        if content:
            content.encode("utf-8")
        return len(arguments) + len(content or "")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise LLMError(
            "接口已响应，但没有返回预期的工具调用；请确认模型支持 tools，普通聊天成功不代表可用于 Coding Agent",
            "MODEL_TEST_TOOLS_UNSUPPORTED",
        ) from None


class _ProbeProvider:
    def __init__(self, profile, payload, timeout, settings):
        self.profile, self.payload, self.timeout = profile, payload, timeout
        self.settings = settings

    async def chat(self, _unused):
        return await post_completion(
            self.profile.base_url, self.profile.api_key, self.payload, self.timeout,
            proxy_url=self.settings.custom_model_proxy_url,
            proxy_base_urls=self.settings.custom_model_proxy_base_urls,
        )


async def run_connection_test(store, settings, profile, target, user_id, identifier, disconnected=None):
    payload = _probe_payload(profile, target)
    started = time.monotonic()
    elapsed = lambda: max(0, round((time.monotonic() - started) * 1000))
    input_chars = len(json.dumps(payload, ensure_ascii=False))
    async with track_call(store, identifier, user_id, input_chars, settings, payload["model"], "custom") as recorded:
        state, code, message = "failed", "INTERNAL_ERROR", "测试暂时无法完成，请稍后重试"
        input_tokens = output_tokens = output_chars = None
        try:
            timeout = min(settings.chat_timeout, 20)
            async with asyncio.timeout(timeout):
                result = await ChatService.generate(_ProbeProvider(profile, payload, timeout, settings), (), disconnected)
            if not isinstance(result, dict):
                raise LLMError("模型服务返回了无法识别的数据", "LLM_INVALID_RESPONSE")
            input_tokens, output_tokens = usage_count(result, "prompt_tokens"), usage_count(result, "completion_tokens")
            output_chars = _validate_probe(result, target)
            state, code = "succeeded", None
            message = ("聊天连接测试通过，接口返回了有效文本。" if target == "chat" else
                       "Coding 工具调用测试通过，模型返回了有效工具请求；本次没有读取文件或执行工具。")
        except TimeoutError:
            state, code, message = "timed_out", "LLM_TIMEOUT", "模型连接测试超时，请检查地址、网络或模型响应速度"
        except LLMError as exc:
            state = "cancelled" if exc.code == "REQUEST_CANCELLED" else "timed_out" if exc.status_code == 504 else "failed"
            code, message = exc.code, str(exc)
        except asyncio.CancelledError:
            state, code = "cancelled", "REQUEST_CANCELLED"
            raise
        except Exception as exc:
            # Never serialize an unexpected provider exception, URL, header or body.
            logger.error("model_test_failed request_id=%s category=%s", identifier, type(exc).__name__)
        finally:
            recorded.state, recorded.error_code = state, code
            recorded.output_chars = output_chars
            recorded.input_tokens, recorded.output_tokens = input_tokens, output_tokens
    return ModelConnectionTestResponse(
        ok=state == "succeeded", target=target, model=payload["model"], elapsed_ms=elapsed(),
        message=message, error_code=code, input_tokens=input_tokens,
        output_tokens=output_tokens, request_id=identifier,
    )
