# 解决 Clash 代理下自定义模型测试失败问题

## 问题现象

- **开启 Clash 代理（TUN/Fake-IP）**：自定义模型测试失败，提示 `LLM_ENDPOINT_BLOCKED`
- **关闭 Clash 代理**：测试通过

## 问题原因

当 Clash 开启 TUN/Fake-IP 模式时：

1. Clash 接管系统 DNS，返回 Fake IP（如 `198.18.x.x` 段）
2. 后端的安全检查发现这不是真实公网 IP
3. 为防止 SSRF 攻击，直接拒绝连接

这是**设计上的安全特性**，不是 bug。

## 解决方案：配置本地代理白名单

### 第一步：查看 Clash 的 HTTP 代理端口

1. 打开 Clash Verge
2. 查看设置中的 **Mixed Port**（混合端口）或 **HTTP Port**
3. 记下端口号，例如 `7890` 或 `7897`

**注意**：不是 SOCKS 端口，也不是 External Controller 端口。

### 第二步：修改 backend/.env 配置

在 `backend/.env` 文件中添加或修改：

```bash
# Clash HTTP/Mixed 端口（改成你实际的端口）
CUSTOM_MODEL_PROXY_URL=http://127.0.0.1:7890

# 你使用的自定义模型 Base URL（必须与页面设置完全一致）
CUSTOM_MODEL_PROXY_BASE_URLS=https://api.openai.com/v1
```

**重要规则**：
- 两个配置**必须同时填写或同时留空**
- `CUSTOM_MODEL_PROXY_BASE_URLS` 必须与页面「模型设置」中的 Base URL **完全匹配**
  - 包括协议（https://）
  - 包括路径（如 /v1）
  - 不包括 /chat/completions
- 多个地址用英文逗号分隔：`https://api.openai.com/v1,https://api.another.com/v1`

### 第三步：重启后端

修改 `.env` 后**必须重启后端**（热重载不会加载环境变量变化）：

```powershell
# 停止当前后端（Ctrl+C）
# 然后重新启动
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

### 第四步：验证

1. 保持 Clash 开启
2. 在「模型设置」中点击「测试聊天」
3. 应该能成功调用

## 工作原理

配置后，匹配白名单的模型请求：
- ✅ 通过 Clash 代理访问（Clash 负责 DNS 解析和出站）
- ✅ 保持 HTTPS 加密和证书验证
- ✅ 不受 Fake IP 检查影响

不在白名单的地址：
- ❌ 仍然走直连并执行公网 IP 检查（安全边界）

## 常见错误

| 错误代码 | 原因 | 解决方法 |
|---------|------|---------|
| `LLM_ENDPOINT_BLOCKED` | Base URL 不在白名单 | 确认 `.env` 中的白名单与页面设置完全一致 |
| `LLM_PROXY_CONNECTION_FAILED` | 无法连接代理 | 确认 Clash 正在运行，端口号正确 |
| `LLM_PROXY_TUNNEL_FAILED` | 代理拒绝 HTTPS 隧道 | 检查 Clash 的代理规则，确保允许该域名 |
| `LLM_PROXY_CONFIG_INVALID` | 配置格式错误 | 检查两项是否同时填写，格式是否正确 |

## 为什么云服务器部署后用户开代理不受影响？

**本地开发**：
- 后端运行在你的电脑上
- Clash 也运行在你的电脑上
- 后端的 DNS 查询被 Clash 接管，返回 Fake IP

**云服务器部署**：
- 后端运行在云服务器上
- 用户的 Clash 运行在用户电脑上
- 后端使用服务器的 DNS，不受用户本地代理影响
- 用户只是通过浏览器访问你的网站，代理不影响服务端调用模型

## 配置示例

### 示例 1：使用 OpenAI

```bash
CUSTOM_MODEL_PROXY_URL=http://127.0.0.1:7890
CUSTOM_MODEL_PROXY_BASE_URLS=https://api.openai.com/v1
```

### 示例 2：使用多个平台

```bash
CUSTOM_MODEL_PROXY_URL=http://127.0.0.1:7890
CUSTOM_MODEL_PROXY_BASE_URLS=https://api.openai.com/v1,https://api.anthropic.com
```

### 示例 3：使用自定义路径

```bash
CUSTOM_MODEL_PROXY_URL=http://127.0.0.1:7890
# 注意：路径必须完全匹配
CUSTOM_MODEL_PROXY_BASE_URLS=https://api.example.com/custom-path/v1
```

## 不想配置代理？

两个选择：

**方案 1：关闭 Clash 的 TUN 模式**
- 改用系统代理模式或规则模式
- 缺点：可能需要手动配置每个应用的代理

**方案 2：仅在测试时关闭 Clash**
- 测试自定义模型时临时关闭 Clash
- 测试通过后再开启
- 缺点：每次测试都要切换

## 安全说明

- 白名单中的域名会通过代理访问，**绕过公网 IP 检查**
- 只添加你**信任的模型平台域名**
- 不要添加任意用户输入的域名
- HTTPS 证书验证仍然有效，保护传输安全
