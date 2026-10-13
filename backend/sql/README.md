# 账号数据库说明

当前使用 **SQLite**，无需单独安装数据库服务。正式数据默认在 `backend/data/zhiyan.sqlite3`，不在 `venv` 或前端文件夹中。

## 初始化

后端启动会先识别旧账号表并执行迁移，再执行 `schema.sql` 补齐表和索引。也可以在项目根目录手动运行：

```powershell
.\venv\Scripts\python.exe backend\init_db.py
```

SQL 脚本是 `backend/sql/schema.sql`；Python 初始化入口和后端启动共用此脚本。重复运行不会删除数据，不会覆盖密码，也不会创建默认账号。首次使用时在网页创建自己的账号。

数据库路径配置保持不变；需要变更位置时，添加 `AUTH_DB_PATH=data/zhiyan.sqlite3`。相对路径以 `backend` 为基准。邮箱发送另外需要 SMTP 和 EMAIL_CODE_SECRET 配置，见后端 README。

## 业务数据表

2026-09-21 新增 `attachments`（所属账号/会话、文件信息、解析文字、状态、额度及过期时间）和 `message_attachments`（消息与附件的关联及顺序）。文件本体在数据库旁的私有 attachments 目录，不存 Base64 到消息表。删除会话会触发附件待清理标记，后台清理磁盘内容。未发送附件 24 小时后回收，失败清理会重试。

| 表 | 字段 | 用途 |
|---|---|---|
| `users` | `id`、`username`、`password_hash`、`created_at`、`email`、`email_verified_at`、`auth_version` | 用户名唯一；已绑定邮箱唯一；auth_version 防止改密期间旧密码登录的竞态 |
| `sessions` | `token_hash`、`user_id`、`expires_at` | 登录会话；`user_id` 外键关联 `users.id` |
| `auth_attempts` | `bucket`、`count`、`reset_at` | 登录/注册、验证码申请和核对的固定窗口配额；邮箱计数键使用摘要 |
| `email_verifications` | `id`、`email`、`purpose`、`user_id`、`code_hash`、`created_at`、`expires_at`、`attempts`、`status` | 注册和重置密码验证码；HMAC 摘要绑定记录 ID、邮箱和用途；单次使用 |
| `schema_migrations` | `version`、`applied_at` | 已执行迁移版本与时间 |
| `model_calls` | `request_id`、`user_id`、`model`、时间、状态、字符数、Token 数和错误类别 | 聊天额度、并发租约与调用统计；不保存问题和回答正文 |
| `user_model_settings` | `user_id`、`enabled`、`base_url`、`api_key_encrypted`、`chat_model`、`coding_model`、`vision_model`、`updated_at` | 每账号一套个人模型配置；Key 为绑定账号和目标地址的 Fernet 密文，不能返回前端 |
| `conversations` | `id`、`user_id`、`title`、`title_custom`、`created_at`、`updated_at`、`last_sequence` | 当前账号的聊天会话、标题和消息序号 |
| `messages` | `id`、`conversation_id`、`client_message_id`、`sequence`、`role`、`content`、`status`、`model`、`request_id`、`reply_to_id`、`source_mode`、`sources_json`、租约、错误和裁剪标志 | 聊天问题与回答；记录资料模式与检索来源，保证顺序和重试幂等，删除会话时级联删除 |
| `attachments` | `id`、`user_id`、`conversation_id`、文件名、类型、大小、额度、状态、解析正文、说明及过期时间 | 私有附件元数据和所有权，原文件另存磁盘 |
| `message_attachments` | `message_id`、`attachment_id`、`position` | 消息与附件的关联及顺序，同会话重提问时可复用 |
| `coding_projects` | `id`、`user_id`、`name`、`source_files_json`、`warnings_json`、时间 | 当前账号导入的原始文本项目快照，不随 Agent 任务修改 |
| `coding_tasks` | `id`、`user_id`、`project_id`、`client_task_id`、`base_task_id`、状态、指令、文件副本、步骤、检查与结果 | 独立编程任务，支持幂等提交、取消、导出和基于结果继续；删除项目级联删除任务 |
| `local_coding_projects` | 用户、客户端项目编号、name、name_request、目录显示名、时间 | 本地项目实体；名称与文件夹名独立，一个项目可以有多个独立对话，不保存浏览器句柄或绝对路径 |
| `local_coding_project_numbers` | user_id、last_number | 每账号最后分配的默认名称编号；删除项目不回收编号 |
| `local_coding_conversations` | 用户、project_id、客户端会话编号、目录显示名、标题、last_turn_index、turn_count、时间 | 项目下的连续编程对话；删除对话不删除项目或目录连接 |
| `local_coding_tasks` | 用户、conversation_id、turn_index、客户端任务编号、授权摘要、指令、状态、模型轮次、上下文、待执行工具、进度、截止时间 | 会话中的一次执行；会话删除时级联删除，真实文件和恢复备份留在用户设备 |

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

迁移依次为 `001_email_verification.sql`（邮箱验证码）、`002_model_calls.sql`（模型调用元数据）、`003_conversation_history.sql`（聊天历史）、`004_attachments.sql`（私有附件）、`005_rag_modes.sql`（资料模式）、`006_coding_agent.sql`（旧 ZIP 编程）、`007_local_coding.sql`（浏览器任务）、`008_local_coding_conversations.sql`（连续项目对话）、`009_user_model_settings.sql`（个人模型配置）、`010_local_coding_projects.sql`（一个本地项目下多个对话）、`011_local_project_names.sql`（项目命名），位于 `backend/sql/migrations/`，当前版本为 **11**。执行器 `backend/app/migrations.py` 将字段变更、数据复制和版本记录放在同一事务中，不自动降级。迁移 008 为每个旧本地任务创建一个会话，并复制重建任务表以增加非空会话外键与轮序，保留原任务编号、内容和结果。009 仅新增配置表；010 为每个旧会话创建项目，项目 ID 沿用旧会话 ID 以兼容浏览器句柄，不按同名目录合并；011 保留原目录显示名作为旧项目名称，新增默认编号计数表。新库直接创建完整结构。本轮只提供脚本与回归用例，未执行真实迁移。

个人模型 Key 使用环境变量 `MODEL_CONFIG_SECRET` 加密，不把主密钥存入 SQLite；仅有数据库不能恢复调用凭据。迁移服务器时需同时安全迁移该主密钥。关闭个人配置保留密文，删除配置移除记录，但不能保证旧备份或 WAL 已安全擦除。详情见 [用户自定义模型配置](../../docs/用户自定义模型配置.md)。

不要单独向旧库执行新 schema.sql 代替迁移，也不要删库重建。`migrate_db.py --backup` 只备份 SQLite，附件目录需另行备份；Qdrant 索引不在其中，详见 [RAG 检索与三模式使用说明](../../docs/RAG检索与三模式使用说明.md)。

旧 ZIP 编程项目的 UTF-8 文件保存在 JSON 文本快照中，不创建额外数据库、不写入 Qdrant。每个任务保留修改前后两个文件集合，可据此生成 diff；不是 Git 仓库，也不会修改宿主机原项目。初版每用户最多保留 10 个项目、100 个任务，请定期导出并删除不用的项目，SQLite 备份应按敏感源码管理。

默认的本地文件夹编程与上述副本模式不同：实际修改用户选中目录，原文件的恢复记录保存在当前站点的浏览器 IndexedDB，不在 SQLite。`local_coding_tasks.messages_json` 在运行期间保存发送给模型的必要源码片段及上下文；终止、取消、过期清理或重启恢复时清空该字段和待执行工具。摘要、步骤、WAL 或数据库备份仍可能含代码，不等同于安全擦除。删除任务记录不删除本地文件或浏览器备份，更多说明见 [本地文件编程与一次授权](../../docs/本地文件编程与一次授权.md)。

连续编程的需求和最终结果通过任务记录长期保存，下一轮由后端从同账号、同会话读取近期文本历史。每账号最多 100 个会话、1000 轮记录，每会话最多 200 轮；删除单轮不会复用旧 turn_index。浏览器目录绑定与数据库历史独立，退出账号会清除绑定但不删除对话；删除会话不删除用户磁盘文件或本地恢复备份。
