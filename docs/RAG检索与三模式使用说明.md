# 知言 AI：RAG 检索与三模式使用说明

更新日期：2026-10-03。本文说明当前代码的实现和本地启用方法；真实百炼账号与 Qdrant 的端到端效果需要在你的电脑上验收。

## 1. 先理解两种模型、一个密钥

`QWEN_MODEL` 是生成回答的聊天模型，例如 `qwen-plus`；`EMBEDDING_MODEL` 是把文档片段和问题转换成数字向量的模型，当前默认 `text-embedding-v4`。两者是不同用途的模型，但当前后端都使用同一个 `DASHSCOPE_API_KEY` 调用阿里云百炼；**不需要为 Embedding 再配置一把独立密钥**。同一把密钥仍须具备该模型的访问权限，且模型、密钥与接入地域必须匹配。换一个 `QWEN_MODEL` 不会自动换掉 Embedding 模型。

Qdrant 是本地向量数据库，不是百炼模型；它不负责生成向量或回答。当前本地 Qdrant 连接不使用百炼密钥。以上模型与密钥规则参见[百炼 API Key 说明](https://help.aliyun.com/zh/model-studio/get-api-key/)和[向量化说明](https://help.aliyun.com/zh/model-studio/embedding)。

## 2. 本地配置与启动

在项目根目录，**只编辑已有的 `backend/.env`，不要用示例文件覆盖真实配置**。若之前没有创建 `.env`，才复制 `backend/.env.example`。至少确认以下项：

```dotenv
DASHSCOPE_API_KEY=你已有的百炼密钥
QWEN_MODEL=qwen-plus
RAG_ENABLED=true
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=zhiyan_documents_v1
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSION=1024
```

`text-embedding-v4` 支持 1024 维，所以默认搭配可以直接使用；如果你的账号或地域用的是另一个向量模型，填写该模型的**准确 ID**，并按它实际支持的维度调整 `EMBEDDING_DIMENSION`。模型及维度以[百炼当前模型文档](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)为准。密钥不要放到 Vue 前端、截图或版本库中。

先启动 Docker Desktop 和原有 Qdrant 容器，在 PowerShell 检查：

```powershell
docker ps -a
Invoke-RestMethod http://127.0.0.1:6333/healthz
```

如果容器已存在但停止，使用 `docker start <实际容器名>`；不要为重试而删除容器的命名卷。升级已有 SQLite 数据库前，先停止后端并备份：

```powershell
.\venv\Scripts\python.exe backend\migrate_db.py --backup
```

该命令将数据库迁移到结构版本 5，并为 SQLite 创建备份；私有附件目录和 Qdrant 数据需另外备份。之后在两个 PowerShell 窗口分别启动：

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

```powershell
Set-Location frontend
pnpm dev
```

后端**不会在启动时预先索引所有文件**；第一次针对当前会话选择“查资料”并发送问题时，会自动创建 Qdrant 集合、切分并索引本会话已绑定的文字文档。因此，刚启动后 `/collections` 为空不一定是错误，首次资料问答会比普通聊天慢。已经绑定的附件在原会话内可继续使用；未绑定附件须随消息发送后才能进入当前会话的检索范围。

## 3. 三种模式和数据流

| 页面模式 | 当前行为 |
| --- | --- |
| 自动 | 浏览器根据明显的文档词、当前上传附件和指代词做轻量判断；明确问资料时查资料，普通问题直接聊天。没有文档且问法含糊时先弹窗让你选；此判断不是另一次模型调用 |
| 直接聊天 | 不检索 Qdrant，也不把文字文档全文塞入模型；仍保留现有聊天历史。图片继续走视觉模型 |
| 查资料 | 只检索当前已登录用户、当前会话内的文字文档；最多处理 10 份文档，取回最多 5 个相似片段，和问题一起发给聊天模型，回答下方保存并展示来源 |

文档处理流程为：上传 TXT/Markdown/文字型 PDF/DOCX → 原有附件解析 → 按页码、标题、段落优先切分，长段落再按句子附近切分（默认约 850 字、重叠 100 字）→ 使用 `EMBEDDING_MODEL` 向量化 → 写入 Qdrant。提问流程为：问题向量化 → Qdrant 过滤用户 ID、会话 ID、附件 ID 和向量模型 → 相似度检索 → 检索片段交给聊天模型生成回答。回答来源和所选模式另存于 SQLite 消息表，方便刷新后仍显示。

SQLite 保存用户、会话、消息、附件归属和原始解析文本，是权限判断的权威来源；Qdrant 保存片段向量及检索用的片段文字和来源信息。后端从 SQLite 计算允许访问的附件集合，不接受前端提供任意用户 ID 去查询。删除附件或会话后，后台清理对应向量；Qdrant 暂时不可用时保留待清理记录，之后重试。图片目前**不做 OCR 或图片向量检索**，扫描版 PDF 若提取不到文字也不能靠这版 RAG 理解页面。

## 4. 向量数据库需要特别注意

1. **模型和维度是一套索引的身份。** 一个 Qdrant 集合内的向量维度必须一致。更换 `EMBEDDING_MODEL` 时，即使新旧模型都输出 1024 维，向量空间也不应混用；同时换一个新的 `QDRANT_COLLECTION` 名，让文档在新集合内按需重建。不要直接删旧集合，先确认新集合可用与备份策略。[Qdrant 集合说明](https://qdrant.tech/documentation/manage-data/collections/)
2. **不要把 Qdrant 端口暴露到公网。** 本项目的本地配置只接受 `localhost` 或 `127.0.0.1`，Docker 端口最好绑定 `127.0.0.1:6333:6333`。自部署 Qdrant 默认没有认证和加密；以后部署到服务器时，需要另做私网访问、认证、TLS 与密钥管理。[Qdrant 安全说明](https://qdrant.tech/documentation/security/)
3. **命名卷不等于备份。** SQLite 备份不包含附件原文件或 Qdrant 向量数据。至少保留 SQLite 和附件目录；若需要快速恢复向量索引，再为 Qdrant 做快照。当前向量可从保留的原文重新生成，但会再次产生 Embedding 调用和等待时间。[Qdrant 快照说明](https://qdrant.tech/documentation/snapshots/)
4. **检索来源不等于答案必然正确。** 第一版是单路密集向量检索，没有重排、相似度门槛或严格的引用逐句校验。页面列出的来源代表模型收到的片段，不保证它支持回答的每一句。重要信息仍须打开原文件核对。
5. **用量分开计算。** 首次索引文档和每次“查资料”提问会调用百炼 Embedding；随后生成回答还会调用聊天模型。“直接聊天”不会产生文档检索的 Embedding 调用。向量数据保留在本地 Qdrant，但发送给百炼做向量化的文本会离开本机，敏感资料须遵守你的数据使用要求。

## 5. 排查顺序

- 页面提示无法连接 Qdrant：先查 `docker ps -a`、`/healthz` 和 `QDRANT_URL`。普通聊天不依赖 Qdrant。
- 提示向量模型失败：检查 `DASHSCOPE_API_KEY`、`EMBEDDING_MODEL` 的准确名称、地域、访问权限及额度；已有聊天成功不保证该密钥有向量模型权限。
- 提示集合维度不符：核对 `EMBEDDING_DIMENSION`，再换新集合名重建，不要直接修改既有集合中的向量维度。
- 文档没有被检索：先确认上传后状态为 ready、文件已随消息绑定在**当前会话**，并且 PDF/DOCX 确有可提取文字。仅打开新会话不会自动检索旧会话文件。
- 首次回答较慢：这次请求可能正在切分、向量化并写入索引；后续提问仍要向量化问题，但通常无需重复索引相同附件。
