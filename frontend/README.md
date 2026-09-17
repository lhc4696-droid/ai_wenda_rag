# 知言 AI 前端

当前界面采用黑白灰底色与蓝色点缀，左侧提供历史会话、切换、重命名和删除，聊天标题居中，切换时显示轻量加载占位与淡入过渡。右上角账号菜单统一提供“账号设置”和“退出登录”；账号资料、安全设置保留在设置弹窗中。改名不再要求输入密码并保留聊天；主动改密仍验证当前密码，当前浏览器换发凭证保持登录，其他登录失效，当前聊天和草稿保留。邮箱找回密码仍要求全部重新登录。详见 [聊天界面与账号交互调整](../docs/聊天界面与账号交互调整.md)、[聊天历史实现与验收](../docs/聊天历史实现与验收.md) 和 [账号功能说明](../docs/账号设置与聊天质量优化.md)。本轮未运行功能测试或构建。

Vue 3 + Vite 单页聊天应用。前端代码、依赖、构建配置和测试均位于 `frontend/`。

已接入邮箱验证码注册、忘记密码、登录和退出。第一次打开页面点击「创建账号」，填写邮箱并申请验证码后注册，无预设账号或默认密码。邮件发送配置只在后端填写，见 [配置与使用说明](D:/code/project_python/wen_da/docs/邮箱验证码与密码找回.md)。

## 本地启动

需要 Node.js 20.19+ 或 22.12+，以及 pnpm。继续使用项目根目录现有的 Python 虚拟环境。

第一个 PowerShell 窗口，启动后端（已有的 `backend/.env` 保持原配置）：

```powershell
cd D:\code\project_python\wen_da
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

第二个 PowerShell 窗口，启动前端：

```powershell
cd D:\code\project_python\wen_da\frontend
pnpm install
pnpm dev
```

浏览器打开 [聊天页面](http://127.0.0.1:5173)。开发端口固定为 5173；若被占用，请先关闭占用该端口的旧前端服务。`pnpm install` 仅在首次安装或依赖变更后需要运行。

## 接口与配置

账号接口为 `/api/auth/register`、`/api/auth/login`、`/api/auth/me`、`/api/auth/logout`，新增 `/api/auth/email-code` 与 `/api/auth/password/reset`。注册需要邮箱和 6 位验证码；找回密码使用已验证邮箱及重置用途验证码。用户名 3–32 位（字母、数字、下划线），密码 8–128 个字符。浏览器用 HttpOnly Cookie 自动携带登录态；不会把密码、验证码或会话令牌写到 localStorage。刷新恢复登录态和地址栏指定的会话；退出、过期或切换账号清空页面状态，服务器历史继续保留。

验证码发送显示“已受理”而非保证送达；倒计时来自服务端返回值，429 会使用 Retry-After。修改邮箱、切换用途会清除输入的验证码。重置密码后返回登录页，不自动登录；旧账号没有已验证邮箱时暂不能使用邮箱找回。

浏览器请求相对地址 `/api/conversations`、`/api/conversations/{id}/messages`、`/api/health`。Vite 在开发和本地预览时代理到 `http://127.0.0.1:8000`。后端地址变化时，可以将 `.env.example` 复制为 `.env.local` 修改 `API_PROXY_TARGET` 并重启 Vite。

千问 API Key **只在 `backend/.env` 中配置**，前端不需要密钥。顶部的服务连接状态只表示健康检查结果，不代表密钥或模型可用；模型错误会在发送问题后显示。

`POST /api/conversations/{id}/messages` 请求（先新建会话，客户端生成一次问题的 UUID）：

```json
{ "content": "你好", "client_message_id": "320bfb5b-3484-45fa-938f-1553676b1904", "expected_last_sequence": 0, "retry": false }
```

响应包含 conversation、user_message、assistant_message、replayed；页面按服务端 ID 合并消息。同一已完成编号重试只读取已保存结果。旧 `/api/chat` 不保存历史，仅供旧客户端兼容。

## 功能与边界

- 后端保存会话与消息、读取上下文；刷新恢复当前会话，新建不删除旧会话。列表每页 20 场、消息每页 50 条，支持加载更多。
- Enter 发送，Shift+Enter 换行；中文输入法选词时不会发送。
- 支持 Markdown、代码块、复制回答和手机布局；模型输出的 HTML 不执行。
- 请求期间禁用重复发送；失败后可重试或修改问题，重试不会重复添加消息。
- 等完整回答返回后显示，当前后端不提供流式接口。
- 每条最多 20,000 字符；不再限制一场对话最多 25 次提问。后端仅选择最近 25 个成功轮次并按字符预算裁剪作为模型上下文，完整历史仍保留。
- 前端等待上限 120 秒。新建对话会放弃旧请求和旧回答，但不能保证百炼已经停止生成或计费。

## 文件职责

```text
frontend/
├── src/
│   ├── App.vue                 # 登录态校验、页面切换、退出及跨标签页同步
│   ├── main.js                 # Vue 挂载入口
│   ├── styles.css              # 桌面/手机样式
│   ├── auth.css                # 登录/注册页面样式
│   ├── account.css             # 右上角账号菜单与设置选项卡样式
│   ├── api/auth.js             # 请求账号接口
│   ├── api/http.js             # Cookie 请求及通用错误处理
│   ├── api/chat.js             # 请求 FastAPI
│   ├── api/conversations.js   # 持久化会话与消息接口
│   ├── composables/useConversations.js # 历史状态、分页、幂等重试、响应恢复和切换隔离
│   ├── composables/useChat.js  # 旧临时聊天兼容逻辑，新页面已不使用
│   ├── components/            # AuthView 登录页、ChatWorkspace 聊天页及消息组件
│   │   ├── AccountMenu.vue    # 账号下拉菜单、悬停/点击/键盘与焦点管理
│   │   └── AccountSettings.vue # 账号资料和安全设置，共用原有接口
│   └── lib/markdown.js         # Markdown 渲染
├── tests/                     # 状态逻辑和浏览器测试
├── public/                    # 网站图标
├── vite.config.js             # Vue 插件、端口和后端代理
├── package.json               # 依赖和命令
└── pnpm-lock.yaml             # 可复现依赖版本
```

## 验证与构建

在 `frontend` 目录执行：

```powershell
pnpm test
pnpm build
pnpm test:e2e
pnpm test:integration
```

浏览器测试默认使用已安装的 Microsoft Edge，自动启动 Vite 并模拟后端回答，不调用百炼，不消耗模型额度。如果只有 Chrome，可先执行 `$env:PLAYWRIGHT_CHANNEL = 'chrome'`。浏览器测试包含桌面和手机尺寸，截图在 `test-results/`。

`test:integration` 使用空闲端口启动临时 FastAPI 和 Vite，连接独立的临时 SQLite，用真实 Cookie 验证邮箱注册、登录、刷新恢复、退出和密码重置后的跨设备撤销。邮件由仅在测试入口中注入的模拟发送器写入临时收件箱，没有取码 HTTP 后门。测试结束关闭本次服务并清理临时数据库和测试邮件，不发送真实邮件、不写入正式数据库，也不调用千问。

构建产物位于 `frontend/dist/`，运行 `pnpm preview` 后可访问 [本地构建预览](http://127.0.0.1:4173)，仍需后端运行，且在后端的 `CORS_ORIGINS` 中允许该预览来源。正式部署时需由托管服务将 `/api` 转发到 FastAPI；Vite 的代理配置不会进入静态构建产物。

技术说明参考 [Vue 官方快速上手](https://vuejs.org/guide/quick-start.html) 和 [Vite 代理配置](https://vite.dev/config/server-options.html#server-proxy)。
