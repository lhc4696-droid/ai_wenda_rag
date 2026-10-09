"""Validated tools; no shell or host filesystem access is exposed to the model."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schemas import CodingError
from .workspace import validate_files, validate_path


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListFiles(Arguments):
    prefix: str = Field(default="", max_length=240)


class ReadFile(Arguments):
    path: str = Field(min_length=1, max_length=240)
    start_line: int = Field(default=1, ge=1, le=200000)
    end_line: int = Field(default=200, ge=1, le=200000)


class SearchCode(Arguments):
    query: str = Field(min_length=1, max_length=200)
    path: str | None = Field(default=None, max_length=240)


class WriteFile(Arguments):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=131072)


class ReplaceText(Arguments):
    path: str = Field(min_length=1, max_length=240)
    old_text: str = Field(min_length=1, max_length=131072)
    new_text: str = Field(max_length=131072)


class DeleteFile(Arguments):
    path: str = Field(min_length=1, max_length=240)


class RunCheck(Arguments):
    kind: Literal["syntax", "pytest"]


SPECS = {
    "list_files": (ListFiles, "列出任务副本的文件路径，可按路径前缀过滤。"),
    "read_file": (ReadFile, "按行读取文件，最多 200 行/12000 字符；修改现有文件前必须先读取。"),
    "search_code": (SearchCode, "在文本文件中搜索字面字符串（不是正则），最多返回 40 个匹配。"),
    "write_file": (WriteFile, "创建一个不存在的新文本文件；不能覆盖现有文件。"),
    "replace_text": (ReplaceText, "精确替换文件中仅出现一次的 old_text；需要先 read_file。"),
    "delete_file": (DeleteFile, "删除已读取的文件，仅作用于任务副本，不影响原项目。"),
    "run_check": (RunCheck, "在无网络 Docker 沙箱运行 syntax 语法检查或 pytest；可能不可用。"),
}

TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": name, "description": description, "parameters": schema.model_json_schema()}}
    for name, (schema, description) in SPECS.items()
]


class CodingTools:
    def __init__(self, files, sandbox, task_id):
        self.files = dict(files)
        self.sandbox, self.task_id = sandbox, task_id
        self.read_paths = set()
        self.revision = 0
        self.checks = []

    def _get(self, path):
        path = validate_path(path)
        if path not in self.files:
            raise CodingError("文件不存在")
        return path, self.files[path]

    def _commit(self, files):
        validate_files(files)
        if files != self.files:
            self.files = files
            self.revision += 1
            for check in self.checks:
                check["stale"] = True

    async def check(self, kind, *, automatic=False):
        if len(self.checks) >= (13 if automatic else 12):
            raise CodingError("已达到单任务检查次数上限，请总结当前结果或继续新任务")
        result = dict(await self.sandbox.run(dict(self.files), kind, self.task_id))
        result.update(revision=self.revision, stale=False)
        self.checks.append(result)
        return result

    async def execute(self, name, raw):
        try:
            if name not in SPECS:
                raise CodingError("未知工具，只能使用提供的工具名称")
            if not isinstance(raw, str) or len(raw) > 300000:
                raise CodingError("工具参数过大或格式错误")
            args = SPECS[name][0].model_validate(json.loads(raw))
            if name == "list_files":
                return {"files": [p for p in sorted(self.files) if p.startswith(args.prefix)]}
            if name == "read_file":
                path, content = self._get(args.path)
                if args.end_line < args.start_line:
                    raise CodingError("end_line 不可小于 start_line")
                lines = content.splitlines()
                end = min(args.end_line, args.start_line + 199)
                text = "\n".join(f"{i + 1}: {lines[i]}" for i in range(args.start_line - 1, min(end, len(lines))))
                self.read_paths.add(path)
                return {"path": path, "content": text[:12000], "total_lines": len(lines), "truncated": len(text) > 12000 or end < min(args.end_line, len(lines))}
            if name == "search_code":
                files = {args.path: self._get(args.path)[1]} if args.path else self.files
                hits = []
                for path, content in sorted(files.items()):
                    for number, line in enumerate(content.splitlines(), 1):
                        if args.query in line:
                            if len(hits) == 40:
                                return {"matches": hits, "truncated": True}
                            position = line.index(args.query)
                            hits.append({"path": path, "line": number, "text": line[max(0, position - 100):position + 300]})
                return {"matches": hits, "truncated": False}
            if name == "run_check":
                return await self.check(args.kind)
            path = validate_path(args.path)
            candidate = dict(self.files)
            if name == "write_file":
                if path in candidate:
                    raise CodingError("文件已存在，请先读取，再用 replace_text 修改")
                candidate[path] = args.content
            else:
                _, original = self._get(path)
                if path not in self.read_paths:
                    raise CodingError("必须先使用 read_file 读取这个文件")
                if name == "delete_file":
                    del candidate[path]
                else:
                    if original.count(args.old_text) != 1:
                        raise CodingError("old_text 必须精确匹配一次；请重新读取文件并提供唯一的上下文")
                    candidate[path] = original.replace(args.old_text, args.new_text, 1)
            self._commit(candidate)
            return {"ok": True, "path": path, "revision": self.revision}
        except (ValidationError, ValueError, TypeError, RecursionError):
            return {"ok": False, "error": "工具参数不符合定义，请检查字段类型、范围与 JSON 格式"}
        except CodingError as exc:
            return {"ok": False, "error": str(exc)}
