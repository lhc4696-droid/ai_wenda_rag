# 旧 ZIP Coding Agent 使用与部署说明（历史归档）

> **旧 ZIP 工作台已下线，本文仅作为历史设计记录保留，不是当前版本的操作指南。** 前端导入/下载入口和旧 ZIP API 已移除，不能再按本文上传 ZIP、创建旧任务或下载副本。当前可用的编程入口是 [本地文件编程与一次授权](本地文件编程与一次授权.md)，不需要 Docker，也不执行命令。

旧 ZIP 下线时没有删除 `coding_projects`、`coding_tasks` 中的历史项目、源码副本或任务记录；迁移 006 继续保留。当前本地会话版已另增迁移 008，数据库版本为 8。保留部分旧后端模块是因为本地模式仍复用其模型调度、校验等逻辑，不代表旧产品入口可用。

当前本地工作台支持一个对话绑定一个目录并连续提出需求，停止本轮不解除绑定；聊天居中、目录和过程在两侧、修改对照按需弹出。后续消息复用有效目录连接与服务端近期会话上下文；浏览器权限失效或主动断开才需重连，退出账号会清除目录绑定。当前操作步骤请看上方指南，不要使用下面的旧 ZIP 流程。

以下内容记录旧版曾采用的结构和边界。旧版允许用户导入小型 Python 项目的 ZIP，输入修改需求，由 Agent 读取代码、搜索、修改任务副本，并调用受限制的代码检查工具，现有账号体系校验项目和任务归属。代码搜索不依赖 Qdrant，未实现 Kotlin 桌面客户端、Git 远程操作、自动部署或通用命令行工具。

## 1. 历史设计：代码和数据怎样流转

```text
浏览器导入项目 ZIP
  → 后端校验归属、文件路径、数量、解压大小和文本格式
  → 保存原始项目文本快照
  → 提交修改需求，创建独立任务副本
  → 模型选择读取 / 搜索 / 修改 / 检查工具
  → 保存工具事件、修改差异和任务结果
  → 用户查看差异，按需下载修改后的 ZIP
```

上传项目原始快照不随任务修改。Agent 修改的是任务自己的文件集合；它不会直接改写本机当前仓库，也不会把 ZIP 直接解压到服务端的任意目录。后续任务可以在已完成任务的副本上继续；导出的 ZIP 由用户决定在哪里解压、如何合并。

项目文本会按任务需要发送给当前配置的百炼模型。上传前请移除密钥、生产数据和不应发送给模型的内容。上传规则还会拦截敏感文件及无关的大型目录，但不能保证识别藏在普通源代码中的所有凭证。

## 2. 历史设计：初版范围与容量

| 项目 | 初版限制 |
| --- | --- |
| 输入格式 | ZIP 中的小型 UTF-8 文本代码项目，以 Python 为主要执行语言 |
| ZIP 上传大小 | 5 MiB |
| 文本文件数量 | 最多 150 个 |
| 单文件大小 | 最多 128 KiB |
| 全部文件解压后文本大小 | 最多 2 MiB |
| 每用户项目数量 | 最多 10 个 |
| 默认任务步数 | 12 步 |
| 默认单任务耗时 | 300 秒 |
| 默认单次代码检查耗时 | 45 秒 |
| 代码检查输出 | 最多保留 32 KiB，超量会停止检查 |

这些是旧版学习与本地开发工作台的边界，不是模型或 Docker 的最大能力，也不表示当前还能导入项目。当前本地文件模式的容量与使用步骤见新版说明。

Agent 可使用的工具包括目录列表、读取文件、代码文本搜索、新增文件、精确替换、删除文件，以及两种固定检查：

- `syntax`：编译检查 `.py` 文件的语法，不运行应用代码；语法通过不代表业务正确。
- `pytest`：在容器内运行测试；未收集到测试会显示未通过，不能当作验证成功。

模型不能提供任意 shell 命令。项目中的 `requirements.txt`、`pyproject.toml` 或安装脚本不会被自动执行。缺少第三方依赖时，检查会明确失败，需由维护者更新可信镜像中的依赖。

## 3. 历史配置记录（不要按本节启用旧工作台）

旧版在 `backend/.env` 中使用项目现有的百炼密钥。下列配置记录包含旧沙箱专用字段，不能整体作为当前启用步骤；`QWEN_CODING_MODEL` 和任务额度等共享配置是否适用，以本地模式文档为准。

```dotenv
# 已有 DASHSCOPE_API_KEY 继续使用，不需要额外的 Coding Agent 密钥。
# QWEN_CODING_MODEL=你账号可调用且支持工具调用的模型

CODING_MAX_STEPS=12
CODING_TASK_TIMEOUT=300
CODING_CHECK_TIMEOUT=45
CODING_TASKS_PER_DAY=20
CODING_GLOBAL_CONCURRENCY=4

# 历史沙箱配置：当前工作台已下线，不要据此开启。
CODING_SANDBOX_ENABLED=false
CODING_SANDBOX_IMAGE=zhiyan-coding-python:1
```

真实密钥不要放进代码、Dockerfile 或项目副本。旧沙箱配置即使保留，也不能恢复已移除的前端和 API。本次停用不新增数据库迁移，不要求重建或删除旧数据库。

## 4. 历史部署记录：代码检查镜像

**下列 Docker 命令和配置仅供理解旧版，不是当前版本可用的工作台启动方案，请勿照做以尝试恢复 ZIP 功能。** 当前本地模式不使用这套检查镜像；构建镜像或开启旧开关都不会恢复旧接口。旧版代码检查默认关闭，后端从不降级为在宿主机执行用户代码。

旧版部署步骤曾为：

1. 启动 Docker Desktop，使用 Linux containers，并让 FastAPI 账户访问 Docker。
2. 在项目根目录使用下列命令和 `backend/coding-sandbox/Dockerfile` 构建专用镜像，与已有 Qdrant 容器无关。

```powershell
docker build -t zhiyan-coding-python:1 backend/coding-sandbox
```

3. 镜像构建成功后，调整 `backend/.env`：

```dotenv
CODING_SANDBOX_ENABLED=true
CODING_SANDBOX_IMAGE=zhiyan-coding-python:1
```

4. 旧版重启 FastAPI 后从 ZIP 工作台创建任务；需要检查时启动临时容器，结束后移除。该入口现已下线，当前版本无法完成这一步。

镜像包含 Python 3.12、pytest 8.3.5、FastAPI 0.141.1（与后端 requirements.txt 对齐）和 httpx 0.28.1，主要用于小型纯 Python/FastAPI 示例。它与本项目后端 venv 仍是独立环境，不继承后端全部依赖。应用运行时使用 `--pull=never`，不会自动拉取缺失的镜像；镜像不存在、Docker 不可用或沙箱未开启时会返回明确的「不可用」结果。

本次下线没有构建镜像、调用真实模型、运行测试或执行真实数据库迁移；不需要用户再联调这套已停用流程。

## 5. 历史设计：沙箱的具体边界

每次检查只给临时容器传递任务文件文本和固定的检查类型：

- 没有宿主机目录挂载，也没有 Docker socket 挂载；文件通过标准输入进入容器的临时文件系统。
- 关闭网络，不注入百炼、邮箱或数据库凭证；以 UID/GID `10001` 运行。
- 根文件系统只读，移除 Linux capabilities，启用 `no-new-privileges`。
- 限制 1 CPU、256 MiB 内存、64 个进程；`/workspace` 使用 32 MiB tmpfs，`/tmp` 使用 16 MiB tmpfs。
- 限制执行时间、打开文件数量、单文件写入大小以及返回输出大小；Docker 容器日志驱动关闭，避免无限日志写满宿主机。
- 用户取消任务或检查超时后，终止本次 Docker 客户端，并按随机唯一名称移除本次容器；不会清理其他任务或 Qdrant 容器。

Docker 守护进程停止响应、服务进程崩溃或机器断电时，清理仍可能无法完成。服务端会记录无法清理的容器名称；管理员应只处理对应 `zhiyan-code-...` 容器，不要用全局容器清理命令代替。

**这套 Docker 限制是本地开发的执行基线，不是已经通过攻击验证的公网多租户沙箱。** 真正上线给不可信用户运行代码前，需要将执行服务放到独立的 worker 主机或更强隔离环境；worker 不应持有业务数据库、生产文件或模型密钥。还需补齐可靠任务队列、崩溃后清理、服务端并发与费用监控、审计和镜像漏洞更新；不应直接给公网 API 进程授予生产 Docker 主机的无限管理权限。

Docker 参数和资源限制的官方依据：[容器运行参数](https://docs.docker.com/reference/cli/docker/container/run/)、[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)。

## 6. 历史使用流程（当前不可用）

以下步骤记录旧版交互，不是当前验收步骤。旧导入、检查与导出入口均已下线。

1. 准备含 `main.py` 和 `tests/test_main.py` 的小型示例项目并压缩为 ZIP。
2. 登录知言，打开「编程工作台」，导入项目。
3. 输入具体需求，例如：“为现有 FastAPI 项目增加 `/api/version` 接口，返回版本号，补充对应测试；保留已有接口。”
4. 观察工具事件和任务状态，检查实际变更文件。模型最后的文字说明不能代替差异和工具结果。
5. 若沙箱关闭或依赖不足，先把检查结果视为未验证，再根据项目情况手动运行检查。
6. 下载 ZIP，人工检查后再合并到正式项目。不要直接用整个 ZIP 覆盖现有仓库。

## 7. 已下线的 API（历史记录）

以下接口已从当前应用移除，不再可用，不能继续按表中用途调用。旧版曾使用现有登录会话，并校验项目和任务归属。

| 接口 | 用途 |
| --- | --- |
| `POST /api/coding/projects` | 导入项目 ZIP |
| `POST /api/coding/projects/{project_id}/tasks` | 提交 `instruction`、`client_task_id`，可选 `base_task_id` |
| `GET /api/coding/tasks/{task_id}` | 查看任务状态、事件及结果 |
| `POST /api/coding/tasks/{task_id}/cancel` | 取消当前任务 |
| `GET /api/coding/tasks/{task_id}/export` | 下载任务文件副本 |

原 `GET /api/coding/capabilities` 也已下线；当前本地模式改用受登录保护的 `GET /api/coding/local/capabilities`。新版任务接口见本地模式文档及当前后端 `/docs`，不要把旧参数表当成当前接口说明。

旧版任务曾在单个后端进程内运行，热重载会中断任务。停用后不会重新启动旧模型循环，旧未终止任务会被标记为中断，已有源码副本继续保留在数据库中。

任务每轮模型调用都会计入现有 `CHAT_REQUESTS_PER_MINUTE` / `CHAT_REQUESTS_PER_DAY` 等额度，默认单用户最多 1 个运行中编程任务、每日 20 个任务，最多保留 100 个任务。短期限流时会等待，但仍占用任务总时间预算；每日额度不足会终止并保留修改。任务数和模型调用次数是不同指标，一项任务通常需要多次模型调用。

“已完成”表示 Agent 工具循环结束，不代表代码质量已经得到保证。只有未过期的检查结果才能反映当前副本；语法通过、测试通过和测试不可用需要分别判断。检查以后再次修改文件，旧结果会标记为“已过期”。

## 8. 历史代码位置与当前保留策略

- 原 `backend/app/coding/routes.py`：曾提供 ZIP 导入/文件/任务/导出接口，现已移除。
- `store.py`、`workspace.py`：保留的任务快照、ZIP 和路径校验代码；本地模式仍复用部分公共逻辑。
- `runtime.py`：公共任务调度、关闭等待、数据库调用辅助和模型计量；不依赖 ZIP 执行工具或 Docker 沙箱。
- `service.py`：保留旧 ZIP 任务执行流程，通过公共运行时复用调度和计量。本地服务直接继承公共运行时，应用启动不再导入旧服务或创建沙箱对象。
- `model.py`、`prompts.py`、`tools.py`：保留模型适配、工具定义等代码，不代表旧 ZIP 工具仍对用户开放。
- `sandbox.py`、`backend/coding-sandbox/Dockerfile`：旧执行检查实现及镜像记录，本地文件模式不执行它们。
- 原 `frontend/src/components/CodingWorkspace.vue` 和 `frontend/src/api/coding.js`：旧界面和旧 API 客户端，现已移除。

既有迁移 `backend/sql/migrations/006_coding_agent.sql` 创建旧模式的 `coding_projects` 和 `coding_tasks`，原始文本和任务文件副本保存在 SQLite 中，不加入 Qdrant。迁移 006 及旧表/旧数据继续保留，既有迁移 `007_local_coding.sql` 创建本地任务表；数据库版本仍为 7。本次下线不增加迁移、不删除旧数据，没有代你启动后端或迁移真实数据库。若从更旧版本升级，仍按既有流程备份并升级到版本 7。

旧 ZIP 接口的集成验收步骤不再适用。`backend/tests/test_coding.py` 中保留的公共逻辑或停用回归用例不表示 ZIP 工作台仍可使用；当前本地任务协议的离线用例为 `backend/tests/test_local_coding.py`，运行时依赖隔离与生命周期用例为 `backend/tests/test_coding_runtime.py`，完整手动验收见本地模式说明。
