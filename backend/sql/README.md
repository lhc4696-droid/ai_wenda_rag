# 账号数据库说明

当前使用 **SQLite**，无需单独安装数据库服务。正式数据默认在 `backend/data/zhiyan.sqlite3`，不在 `venv` 或前端文件夹中。

## 初始化

后端启动会先识别旧账号表并执行迁移，再执行 `schema.sql` 补齐表和索引。也可以在项目根目录手动运行：

```powershell
.\venv\Scripts\python.exe backend\init_db.py
```

SQL 脚本是 `backend/sql/schema.sql`；Python 初始化入口和后端启动共用此脚本。重复运行不会删除数据，不会覆盖密码，也不会创建默认账号。首次使用时在网页创建自己的账号。

数据库路径配置保持不变；需要变更位置时，添加 `AUTH_DB_PATH=data/zhiyan.sqlite3`。相对路径以 `backend` 为基准。邮箱发送另外需要 SMTP 和 EMAIL_CODE_SECRET 配置，见后端 README。

## 八张表

| 表 | 字段 | 用途 |
|---|---|---|
| `users` | `id`、`username`、`password_hash`、`created_at`、`email`、`email_verified_at`、`auth_version` | 用户名唯一；已绑定邮箱唯一；auth_version 防止改密期间旧密码登录的竞态 |
| `sessions` | `token_hash`、`user_id`、`expires_at` | 登录会话；`user_id` 外键关联 `users.id` |
| `auth_attempts` | `bucket`、`count`、`reset_at` | 登录/注册、验证码申请和核对的固定窗口配额；邮箱计数键使用摘要 |
| `email_verifications` | `id`、`email`、`purpose`、`user_id`、`code_hash`、`created_at`、`expires_at`、`attempts`、`status` | 注册和重置密码验证码；HMAC 摘要绑定记录 ID、邮箱和用途；单次使用 |
| `schema_migrations` | `version`、`applied_at` | 已执行迁移版本与时间 |
| `model_calls` | `request_id`、`user_id`、`model`、时间、状态、字符数、Token 数和错误类别 | 聊天额度、并发租约与调用统计；不保存问题和回答正文 |
| `conversations` | `id`、`user_id`、`title`、`title_custom`、`created_at`、`updated_at`、`last_sequence` | 当前账号的聊天会话、标题和消息序号 |
| `messages` | `id`、`conversation_id`、`client_message_id`、`sequence`、`role`、`content`、`status`、`model`、`request_id`、`reply_to_id`、租约、错误和裁剪标志 | 聊天问题与回答；保证顺序和重试幂等，删除会话时级联删除 |

一个用户可以拥有多条登录会话。浏览器保存随机会话令牌，数据库保存其 SHA-256 摘要；退出时删除对应会话，过期的会话不能继续访问聊天接口。

密码使用 PBKDF2-HMAC-SHA256（600,000 次迭代、每个密码独立随机盐），不会存储明文。所有账号数据查询使用参数化 SQL，具体增删查语句位于 `backend/app/auth/store.py`。

历史记录通过 conversations、messages 保存。刷新、退出、改名或改密不会删除已保存的消息；退出与账号切换只清除当前页面状态。用户可确认删除自己的会话及消息，记录否则持续保留。每次读取、改名、删除和发送均检查会话所有者。

个人设置修改用户名不会改变用户 ID 或清空聊天。主动修改密码会增加认证版本、撤销全部登录会话并使未使用的邮箱验证码失效。模型调用配额在同一 SQLite 写事务内检查和占用，受理后的失败、取消和超时也计入额度。

## 邮箱和验证码规则

- 邮箱域名规范化为小写，本地部分保留大小写；不擅自合并点号、加号别名；数据库和所有接口采用相同规则。
- 旧账号的 email 与 email_verified_at 保持 NULL，密码、ID 和已有登录会话保留；没有已验证邮箱的账号不能通过忘记密码功能找回。本次不自动绑定或认领旧账号。
- 新账号必须核对注册验证码后创建，创建与验证码消耗在同一个写事务内完成。
- 重置验证码绑定发起时的用户 ID；成功后原子更新密码、增加 auth_version、消耗验证码并撤销该账号全部登录会话。
- 同一邮箱、同一用途最多有一条 pending/active 记录。重新申请会使旧记录 superseded；过期、错误达到上限、已使用的码不能核验。
- status 含 pending（等待投递）、active（SMTP 已接受，可核验）、failed、used、superseded。SMTP 接受不等于收件箱已送达。
- 验证码错误次数提交后才返回错误；不会因为异常回滚而丢失次数。
- 过期超过一天的验证码记录会在新的发送请求中清理；密码重置通知不包含新密码。

## 备份与后续变更

`backend/data/` 已加入 Git 忽略规则，不应随代码提交。SQLite 使用 WAL；备份时应停止后端后复制数据库，或使用 SQLite 的备份接口，避免只复制活跃数据库的主文件而遗漏 WAL 中的内容。

脚本针对 SQLite，不应直接导入 MySQL。`CREATE TABLE IF NOT EXISTS` 只负责初始化；以后修改已存在的字段时需要另写迁移脚本。

首次升级建议先备份再迁移（使用 SQLite 在线备份，不遗漏 WAL）：

```powershell
.\venv\Scripts\python.exe backend\migrate_db.py --backup
```

迁移定义位于 `backend/sql/migrations/001_email_verification.sql`（邮箱验证码）、`002_model_calls.sql`（模型调用元数据）和 `003_conversation_history.sql`（聊天历史），当前结构版本为 3，执行器为 `backend/app/migrations.py`。字段变更、建表和版本记录处于同一事务，按顺序升级，重复启动安全；不自动降级更新版本的数据库。不要单独向旧库执行新的 schema.sql 来代替迁移。
