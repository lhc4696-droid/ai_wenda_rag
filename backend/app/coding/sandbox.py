"""Run the two supported Python checks in disposable, restricted containers.

Uploaded code is never imported or executed by the API process. Docker being
unavailable is a reported result, not permission to fall back to host execution.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import threading
import time
import uuid


logger = logging.getLogger(__name__)
MAX_OUTPUT_BYTES = 32 * 1024
MAX_WIRE_BYTES = 8 * 1024 * 1024

# This program is supplied by the application, never by the model. All file
# creation and pytest execution happen inside the container's ephemeral tmpfs.
_RUNNER = r'''
import json
import os
from pathlib import Path, PurePosixPath
import sys

def main():
    raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("project input is too large")
    data = json.loads(raw)
    files, kind = data["files"], data["kind"]
    if kind not in {"syntax", "pytest"} or not isinstance(files, dict):
        raise ValueError("unsupported check or project")
    if not 1 <= len(files) <= 150:
        raise ValueError("invalid project file count")
    total = 0
    for name, content in files.items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("project files must be text")
        if (not name or len(name) > 240 or name.startswith("/")
                or "\\" in name or ":" in name
                or any(ord(c) < 32 for c in name)
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or PurePosixPath(name).is_absolute()):
            raise ValueError("invalid project path")
        size = len(content.encode("utf-8"))
        if size > 128 * 1024:
            raise ValueError("project file is too large")
        total += size
    if total > 2 * 1024 * 1024:
        raise ValueError("project is too large")
    root = Path("/workspace")
    for name, content in files.items():
        target = root / name
        if not target.resolve().is_relative_to(root):
            raise ValueError("project path leaves workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    os.chdir(root)
    if kind == "syntax":
        checked, failed = 0, False
        for name, content in sorted(files.items()):
            if not name.endswith(".py"):
                continue
            checked += 1
            try:
                compile(content, name, "exec")
            except (SyntaxError, ValueError) as error:
                failed = True
                print("{}:{}:{}: {}".format(name, getattr(error, "lineno", 0),
                      getattr(error, "offset", 0), getattr(error, "msg", str(error))))
        print("Checked {} Python files (syntax only; no application code executed).".format(checked))
        if checked == 0:
            print("No Python files found.")
            return 5
        return 1 if failed else 0
    # Import the trusted installed pytest before exposing the project's import
    # path. conftest.py/tests can execute arbitrary code, confined to this container.
    import pytest
    sys.path.insert(0, str(root))
    return int(pytest.main(["-q", "--tb=short", "--disable-warnings",
                          "-c", "/dev/null", "--rootdir=/workspace",
                          "--basetemp=/tmp/pytest", "/workspace"]))

try:
    raise SystemExit(main())
except (ValueError, KeyError, TypeError, OSError) as error:
    print("Check setup failed: {}".format(type(error).__name__))
    raise SystemExit(2)
'''


def _result(kind: str, status: str, output: str, exit_code: int | None = None) -> dict:
    return {"kind": kind, "status": status, "exit_code": exit_code, "output": output}


class DockerSandbox:
    def __init__(self, settings):
        self.settings = settings

    async def run(self, files: dict[str, str], kind: str, task_id: str) -> dict:
        if kind not in {"syntax", "pytest"}:
            return _result(kind, "unavailable", "仅支持 syntax 和 pytest 检查。")
        if not self.settings.coding_sandbox_enabled:
            return _result(kind, "unavailable", "代码检查沙箱尚未启用；本次修改未经运行验证。")
        docker = shutil.which("docker")
        if not docker:
            return _result(kind, "unavailable", "服务端找不到 Docker；不会在宿主机执行代码。")
        image = self.settings.coding_sandbox_image
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,200}", image):
            return _result(kind, "unavailable", "代码检查镜像名称配置不正确。")
        # Do not put a client-controlled task ID in a Docker resource name. The
        # unique name below belongs solely to this invocation and cleanup.
        container = "zhiyan-code-" + uuid.uuid4().hex
        stop = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(
            self._run, docker, image, container, files, kind, stop,
        ))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            # Cancelling to_thread alone does not stop its thread/container.
            # The worker polls stop and performs bounded cleanup in its finally.
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                pass
            raise

    def _run(self, docker, image, container, files, kind, stop):
        try:
            if not isinstance(files, dict) or not 1 <= len(files) <= 150:
                raise ValueError()
            total = 0
            for path, content in files.items():
                if (not isinstance(path, str) or len(path) > 240
                        or not isinstance(content, str) or len(content) > 128 * 1024):
                    raise ValueError()
                size = len(content.encode("utf-8"))
                if size > 128 * 1024:
                    raise ValueError()
                total += size
            if total > 2 * 1024 * 1024:
                raise ValueError()
            payload = json.dumps({"files": files, "kind": kind}, ensure_ascii=False).encode("utf-8")
            if len(payload) > MAX_WIRE_BYTES:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            return _result(kind, "failed", "项目文件超出沙箱允许的文本大小或数量。")
        if stop.is_set():
            return _result(kind, "failed", "代码检查已取消。")

        command = [
            docker, "run", "--rm", "--interactive", "--pull=never",
            "--name", container, "--label", "zhiyan.coding-sandbox=true",
            "--network=none", "--read-only", "--user=10001:10001",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=64", "--cpus=1", "--memory=256m", "--memory-swap=256m",
            "--ulimit", "nofile=128:128", "--ulimit", "fsize=16777216:16777216",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
            "--shm-size=8m", "--log-driver=none", "--workdir=/workspace",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
            "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--env", "HOME=/tmp",
            "--entrypoint", "python", image, "-I", "-B", "-u", "-c", _RUNNER,
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = None
        reader = writer = None
        output = bytearray()
        overflow = threading.Event()
        failure = None
        code = None
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, creationflags=flags, bufsize=0)

            def read_output():
                try:
                    while chunk := proc.stdout.read(4096):
                        remaining = MAX_OUTPUT_BYTES - len(output)
                        output.extend(chunk[:max(remaining, 0)])
                        if len(chunk) > remaining:
                            overflow.set()
                except (OSError, ValueError):
                    pass

            def send_input():
                try:
                    # Unbuffered pipes can perform short writes.
                    remaining = memoryview(payload)
                    while remaining and not stop.is_set():
                        written = proc.stdin.write(remaining)
                        if not written:
                            break
                        remaining = remaining[written:]
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass

            reader = threading.Thread(target=read_output, daemon=True)
            writer = threading.Thread(target=send_input, daemon=True)
            reader.start()
            writer.start()
            deadline = time.monotonic() + self.settings.coding_check_timeout
            while proc.poll() is None:
                if stop.is_set():
                    failure = "代码检查已取消。"
                    break
                if overflow.is_set():
                    failure = "检查输出超过 32 KiB，已停止执行并截断输出。"
                    break
                if time.monotonic() >= deadline:
                    failure = "代码检查超时，已停止执行。"
                    break
                stop.wait(0.1)
            code = proc.poll()
        except OSError:
            return _result(kind, "unavailable", "无法启动 Docker 代码检查，请检查服务端 Docker 配置。")
        finally:
            if proc is not None:
                # Kill the client first so it cannot create a new container after
                # removal. Always target only this invocation's random name.
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                try:
                    subprocess.run([docker, "rm", "--force", container], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=5, creationflags=flags, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    logger.warning("Coding sandbox cleanup unavailable for container %s", container)
                if reader is not None:
                    reader.join(timeout=1)
                if writer is not None:
                    writer.join(timeout=1)
                for stream in (proc.stdin, proc.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

        rendered = bytes(output).decode("utf-8", errors="replace")
        if not failure and overflow.is_set():
            failure = "检查输出超过 32 KiB，结果未被视为通过。"
        if failure:
            return _result(kind, "failed", (rendered + "\n" + failure).strip(), code)
        if code in {125, 126, 127}:
            # Do not echo Docker daemon errors, which may reveal host paths.
            return _result(kind, "unavailable", "Docker 或检查镜像不可用；请启动 Docker 并预先构建检查镜像。", code)
        if code == 5 and kind == "pytest":
            rendered += "\n未收集到测试，不能视为测试通过。"
        return _result(kind, "passed" if code == 0 else "failed", rendered, code)
