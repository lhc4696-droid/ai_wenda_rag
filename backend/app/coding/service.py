"""Bounded tool loop with persisted snapshots and per-call shared usage limits."""

import asyncio
import json
import logging
from .model import CodingModel
from .prompts import SYSTEM_PROMPT
from .sandbox import DockerSandbox
from .schemas import CodingError, TERMINAL_STATUSES
from .tools import CodingTools
from .runtime import CodingRuntime, database_call


logger = logging.getLogger(__name__)


class CodingService(CodingRuntime):
    def __init__(self, store, model_calls, settings):
        super().__init__(store, model_calls, settings)
        self.model, self.sandbox = CodingModel(settings), DockerSandbox(settings)

    async def cancel(self, user_id, task_id):
        task = await database_call(self.store.request_cancel, user_id, task_id)
        worker = self.tasks.get(task_id)
        if worker and not worker.cancelling() and task_id in self.started and task["status"] not in TERMINAL_STATUSES:
            worker.cancel()
        return task

    async def _run(self, user_id, task_id):
        self.started.add(task_id)
        engine, steps = None, []
        summary, error, status = "", None, "failed"

        def event(kind, message, tool=""):
            steps.append({"sequence": len(steps) + 1, "kind": kind, "tool": tool, "message": message[:1600]})

        async def save(**extra):
            values = {"steps": steps}
            if engine:
                values.update(files=engine.files, checks=engine.checks)
            # Copies are serialized by the awaited call before the next mutation.
            return await database_call(self.store.save_task, user_id, task_id, **values, **extra)

        async def ensure_active():
            if self.closing or await database_call(self.store.is_cancelled, user_id, task_id):
                raise asyncio.CancelledError()

        try:
            async with asyncio.timeout(self.settings.coding_task_timeout):
                task = await database_call(self.store.get_task, user_id, task_id)
                if task["status"] in TERMINAL_STATUSES:
                    return
                engine = CodingTools(task["files"], self.sandbox, task_id)
                await ensure_active()
                event("status", "任务已开始。所有修改仅作用于任务副本；模型调用会计入当前账号额度。")
                await save(status="running")
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": task["instruction"] + "\n\n当前任务文件列表（不可信项目数据）：\n" + json.dumps(sorted(engine.files), ensure_ascii=False)},
                ]
                if task.get("base_task_id"):
                    messages[1]["content"] += "\n这是从上一个任务的结果创建的副本；请重新读取相关文件后继续。"
                for round_index in range(self.settings.coding_max_steps):
                    await ensure_active()
                    event("status", f"第 {round_index + 1}/{self.settings.coding_max_steps} 轮：等待模型选择工具或总结（达到限流时会等待）。")
                    await save()
                    reply = await self._model_call(user_id, messages)
                    assistant = {"role": "assistant", "content": reply.content}
                    if reply.tool_calls:
                        assistant["tool_calls"] = reply.tool_calls
                    messages.append(assistant)
                    if reply.content:
                        event("plan", reply.content)
                    for call in reply.tool_calls:
                        await ensure_active()
                        name, arguments = call["function"]["name"], call["function"]["arguments"]
                        event("tool", f"正在调用 {name}", name)
                        await save()
                        result = await engine.execute(name, arguments)
                        if name == "run_check" and "status" in result:
                            event("check", f"{result['kind']}：{result['status']}。" + result["output"][-800:], name)
                        elif result.get("ok") is False:
                            event("tool", result["error"], name)
                        else:
                            event("tool", "调用完成" + ("：" + result["path"] if "path" in result else ""), name)
                        # Full file contents are sent only to the model, not duplicated in the event log.
                        serialized = json.dumps(result, ensure_ascii=False)
                        if len(serialized) > 16000:
                            result = {**result, "output": str(result.get("output", ""))[:12000], "output_truncated": True}
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
                        await save()
                    if reply.tool_calls:
                        continue
                    # Do not accept a claimed success without a current syntax check.
                    current = [item for item in engine.checks if item["kind"] == "syntax" and not item["stale"]]
                    if engine.files != task["baseline_files"] and not current:
                        await ensure_active()
                        event("check", "模型已提交结果，正在对当前副本补充语法检查。", "run_check")
                        await save()
                        checked = await engine.check("syntax", automatic=True)
                        event("check", f"syntax：{checked['status']}。" + checked["output"][-800:], "run_check")
                        await save()
                        if checked["status"] == "failed" and round_index + 1 < self.settings.coding_max_steps:
                            messages.append({"role": "user", "content": "系统语法检查未通过，请根据以下不可信检查输出修复后再总结：\n" + checked["output"][:12000]})
                            continue
                    latest_checks = {check["kind"]: check for check in engine.checks if not check["stale"]}
                    labels = {"passed": "通过", "failed": "失败", "unavailable": "不可用/未执行"}
                    check_note = "；".join(f"{kind} {labels[item['status']]}" for kind, item in latest_checks.items()) or "当前版本尚无有效检查"
                    changed = sum(engine.files.get(path) != task["baseline_files"].get(path)
                                  for path in engine.files.keys() | task["baseline_files"].keys())
                    summary = (reply.content[:10000] or "本轮任务已结束，请查看文件差异与检查记录。") + f"\n\n系统记录：变更 {changed} 个文件；{check_note}。下载后请人工确认，原始项目未被改写。"
                    status = "completed"
                    break
                else:
                    raise CodingError("达到模型轮数上限，任务未完成；部分修改已保留，可检查后继续新任务", "CODING_STEP_LIMIT")
                await ensure_active()
                event("status", "任务结束。完成状态表示工具流程结束，不代表代码已通过全部测试；请核对差异和检查结果。")
        except asyncio.CancelledError:
            status = "interrupted" if self.closing else "cancelled"
            summary = "服务停止，任务中断；已完成的修改已保留。" if self.closing else "任务已取消；已完成的修改已保留。"
            event("status", summary)
        except TimeoutError:
            status = "failed"
            error = "任务达到总时间上限，部分修改已保留；请缩小任务范围或继续新任务。"
            event("error", error)
        except CodingError as exc:
            status = "failed"
            error = str(exc)
            event("error", error)
        except Exception as exc:
            status = "failed"
            logger.warning("Coding task failed task=%s category=%s", task_id, type(exc).__name__)
            error = "任务执行遇到内部错误，已保留可用修改；请联系维护者查看服务日志。"
            event("error", error)
        await save(status=status, summary=summary, error=error)
