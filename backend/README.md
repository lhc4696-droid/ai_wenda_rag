# 知言 AI 后端

当前版本 **0.5.0**：新增持久化聊天历史、会话分页、重命名与删除，保留个人账号设置、调用限额与上下文保护。说明与手动验收见 [聊天历史实现与验收](../docs/聊天历史实现与验收.md)。本轮未运行功能测试。

这是知言 AI 的 FastAPI 后端，提供邮箱验证码注册、忘记密码、登录、退出、会话校验、健康检查和多轮对话接口。

**升级提示：新注册现在必须填写邮箱验证码。原有账号仍可用用户名和密码登录；没有绑定邮箱的旧账号暂不能通过邮箱找回。真实邮件需要配置 SMTP 和 EMAIL_CODE_SECRET。** 详细步骤见 [邮箱验证码与密码找回](D:/code/project_python/wen_da/docs/邮箱验证码与密码找回.md)。

## 1. 配置

仅在尚未创建 `backend/.env` 时，在项目根目录执行（已有配置请保留）：

```powershell
Copy-Item backend/.env.example backend/.env
```

然后编辑 `backend/.env`，填入真实的 `DASHSCOPE_API_KEY`。真实 `.env` 已被 `.gitignore` 排除。

## 2. 安装依赖

```powershell
.\venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

## 3. 启动

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- API 文档：<http://127.0.0.1:8000/docs>

## 4. 对话接口

聊天接口需要先登录。打开前端的「创建账号」，注册后会自动进入聊天页面；没有预设账号或默认密码。

用户名为 3–32 位英文字母、数字或下划线，不区分字母大小写；密码为 8–128 个字符，不能全部为空格。浏览器通过 HttpOnly Cookie 保存登录态，默认有效期 7 天；退出后服务端立即撤销当前会话。

| 接口 | 请求/响应 |
|---|---|
| `POST /api/auth/email-code` | `email`、`purpose`（register 或 reset_password）；202 表示受理，不代表投递成功；返回等待间隔，不返回验证码 |
| `POST /api/auth/register` | `username`、`password`、`email`、`code`；201 返回 user 并设置 Cookie |
| `POST /api/auth/password/reset` | `email`、`code`、`new_password`；200 表示重置成功，撤销全部旧会话，不自动登录 |
| `POST /api/auth/login` | `username`、`password`；成功返回 `200` 和 `user`，重新生成会话 |
| `GET /api/auth/me` | 返回当前 `user`；未登录/过期返回 `401` |
| `POST /api/auth/logout` | 撤销当前会话、清除 Cookie，返回 `204`；重复退出也成功 |
| `POST /api/auth/username` | 登录后只提交 username，更新自己的登录用户名并保留现有会话；保留来源校验、频率限制和重名检查 |
| `POST /api/auth/password/change` | 登录后提交 current_password、new_password；原子撤销旧会话并换发当前浏览器的 HttpOnly Cookie，当前浏览器保持登录，其他登录失效 |
| `GET /api/chat/usage` | 当前用户今日调用次数、已知用量及额度重置时间 |
| `POST /api/conversations` | 新建会话，客户端 UUID id 可用于重试同一次创建 |
| `GET /api/conversations` | 按 limit、cursor 分页读取自己的会话 |
| `GET /api/conversations/{id}/messages` | 按 limit、before 读取消息，默认最近 50 条 |
| `PATCH /api/conversations/{id}` | 提交 title 重命名 |
| `DELETE /api/conversations/{id}` | 确认后删除会话及全部消息，成功 204 |
| `POST /api/conversations/{id}/messages` | content、client_message_id、expected_last_sequence、retry；由后端读取上下文并保存问答 |
| `POST /api/chat` | 已弃用的兼容接口；登录后可调用，但不保存历史 |

在 Swagger 中先执行注册或登录，使用同一浏览器、同一主机地址的 `/docs` 测试聊天，浏览器会自动带上 Cookie。不要混用 `localhost` 和 `127.0.0.1` 的登录状态。

重复用户名返回 `409`，错误用户名/密码返回统一的 `401`，参数错误返回 `422`，来源不受信任返回 `403`，超过注册/登录次数返回 `429`（含 `Retry-After`）。模型服务错误仍返回 `503`。

限流使用 5 分钟固定窗口：每个连接 IP 最多 40 次账号请求、10 次注册；每个用户名最多 10 次登录尝试。开发代理下所有请求通常共用本机 IP。

`POST /api/chat`

请求示例：

```json
{
  "messages": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，有什么可以帮助你？"},
    {"role": "user", "content": "请记住我叫小明"}
  ]
}
```

以上是旧兼容接口示例，不保存历史。新网页使用会话消息接口，只提交当前问题，由后端从数据库读取最近 25 个成功轮次，再按字符预算裁剪；问题与回答分别保存，回答最多 20000 字符并提示截断。完整历史不受 25 轮限制。model_calls 仍仅保存调用元数据。模型总超时默认 90 秒，默认每用户每分钟 10 次、每日 100 次、并发 1 个，全站并发 4 个、每日 1000 次；受理后的失败也计次，日额度于 UTC 零点重置。

## 5. 测试

```powershell
.\venv\Scripts\python.exe -m pytest backend\tests
```

测试使用临时数据库和模拟模型，不写入真实账号数据库、不调用百炼。项目根目录的 `pytest.ini` 已配置后端模块路径。

## 6. 数据库与 SQL

默认 SQLite 文件为 `backend/data/zhiyan.sqlite3`，启动时自动创建或迁移到版本 3。建表脚本为 `backend/sql/schema.sql`，包含 `users`、`sessions`、`auth_attempts`、`email_verifications`、`schema_migrations`、`model_calls`、`conversations`、`messages`；结构解释见 [数据库说明](D:/code/project_python/wen_da/backend/sql/README.md)。升级前推荐运行 `backend/migrate_db.py --backup` 备份。也可手动初始化：

```powershell
.\venv\Scripts\python.exe backend\init_db.py
```

重复运行会保留已有数据，不会创建默认账号。以下账号配置均有默认值；原来的 `.env` 仍可支持旧账号登录，新增邮件功能需要补充示例中的配置：

```dotenv
AUTH_DB_PATH=data/zhiyan.sqlite3
SESSION_MAX_AGE=604800
SESSION_COOKIE_SECURE=false
```

`SESSION_COOKIE_SECURE=false` 用于本地 HTTP。正式 HTTPS 部署时改成 `true`，并将实际前端来源加入 `CORS_ORIGINS`。使用本地构建预览时，也需加入其来源 `http://127.0.0.1:4173`；修改配置后重启后端。

密码采用独立随机盐和 PBKDF2-HMAC-SHA256（600,000 次迭代），会话采用服务端随机令牌；接口检查 Origin/Referer，参数校验失败不会回显密码。依据：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
