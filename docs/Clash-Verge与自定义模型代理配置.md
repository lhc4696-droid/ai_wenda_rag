# Clash Verge 与自定义模型代理配置

更新日期：2026-10-12

## 1. 本次解决什么

自定义模型原先只使用公网直连检查。开启 Clash Verge 的 TUN/Fake-IP 后，系统 DNS 可能返回保留网段中的虚拟 IP，后端在连接模型之前就将其拦截。

现在可以显式配置后端本机 HTTP/Mixed 代理，让指定的可信模型基础地址通过 HTTP CONNECT 隧道连接。后端不再为这些目标使用本机 Fake-IP 解析结果，而由代理完成目标解析和连接；目标仍然是 HTTPS，保留证书及域名验证。

本次覆盖个人 API 的测试聊天、测试 Coding、普通聊天、RAG 答案生成、可选视觉模型以及 Coding Agent。默认百炼 SDK、Embedding、邮件与 Qdrant 的调用方式不变。不需要迁移数据库或增加依赖。

代码已提供配置能力，但没有替你填写真实 `.env`、检测端口、重启服务或调用真实模型。完成下面配置并实际测试后，才能确认你的环境已恢复。

## 2. 查看 Clash Verge 的端口

1. 保持 Clash Verge 运行，TUN/Fake-IP 可以继续开启。
2. 在客户端设置或当前生效配置中查看 **Mixed Port / 混合端口** 或 **HTTP Port / HTTP 端口**。不同版本的界面位置可能不同；配置字段通常是 `mixed-port` 或 `port`。
3. 记录后端电脑上实际监听的端口。不要使用 External Controller / 控制器端口，也不要使用仅支持 SOCKS 的端口。

下文 `7897` 只是示例，不是自动检测到的值。首次实现仅支持无认证的本机 HTTP/Mixed 代理：`http://127.0.0.1:端口`、`http://[::1]:端口`；`localhost` 会规范化为 `127.0.0.1`。不支持 SOCKS URL、远程代理、代理账号密码或路径。

这里的 `http://` 指连接本机代理的协议，**不代表模型请求改为明文 HTTP**：访问模型平台时仍使用经过证书验证的 HTTPS 隧道。

## 3. 配置后端

在 `backend/.env` 中添加或更新以下两项，不要覆盖原有 API Key、加密密钥等配置：

```dotenv
# 示例：请改成 Clash Verge 实际的 HTTP/Mixed 端口
CUSTOM_MODEL_PROXY_URL=http://127.0.0.1:7897

# 示例地址不可直接用于测试：替换成页面“我的 API”中真实可信的完整 Base URL
CUSTOM_MODEL_PROXY_BASE_URLS=https://api.your-provider.com/v1
```

规则如下：

- 两项必须同时填写或同时留空；格式错误会在启动时明确报错，不会悄悄忽略。
- 允许列表最多 32 个地址，多个用英文逗号分隔，不能有空条目、通配符或凭据。
- 填完整基础地址，保留 `/v1`、`/compatible-mode/v1` 等实际前缀，不含 `/chat/completions`。
- 与页面的 Base URL **精确匹配**，包含协议、域名、端口和路径。域名大小写、默认 HTTPS 443 端口和末尾斜杠会规范化；路径大小写、其他路径或子域名不是同一配置。
- 只有匹配列表的地址走显式代理。其他地址继续使用公网直连检查，不会借用代理绕过检查。
- 显式代理失败不会自动回退直连，也不会自动重试模型调用。
- 自定义模型不自动继承 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 或桌面系统代理设置；本次使用上面两项明确配置。

例如页面使用 `https://某平台域名/compatible-mode/v1`，允许列表也必须包含这一完整基础地址，不能只填域名或 `/v1`。

页面的 **Base URL 仍然填写模型平台地址**，不是 `http://127.0.0.1:7897`；API Key 仍然填写模型平台的密钥。

## 4. 重启并由用户验证

修改 `.env` 后需真正重启后端，不能只刷新页面；开发服务器的代码热更新不保证监听 `.env`。

在原后端终端按 `Ctrl+C` 停止，再从项目根目录启动：

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

然后进入模型设置，确认 Base URL 与代理允许列表一致，点击“测试聊天”；通过后再按需“测试 Coding”。真实测试可能产生少量模型费用，也计入项目调用额度。

建议验收：

- [ ] Clash Verge 保持开启，配置匹配的个人模型聊天测试通过。
- [ ] Coding 工具调用测试通过，再检查实际聊天和编程任务。
- [ ] 关闭 Clash Verge 后，显示代理连接失败，不悄悄直连。
- [ ] 不在允许列表的目标仍受原有公网 IP 检查。
- [ ] HTTP、本机或内网模型地址仍不能作为个人 Base URL 保存或请求。
- [ ] 默认百炼与 Embedding 配置没有被本次代理功能改写。

想恢复原行为时，将两项同时留空并重启；不用删除个人模型配置，也不用重新生成加密密钥。

## 5. 错误说明

| 错误类别 | 含义与处理 |
| --- | --- |
| `LLM_ENDPOINT_BLOCKED` | 当前走直连检查且 DNS 返回禁止地址；核对完整 Base URL 是否在允许列表，配置是否已重启加载 |
| `LLM_PROXY_CONFIG_INVALID` | 后端代理配置无效；两项需配对，代理地址与允许列表格式需正确 |
| `LLM_PROXY_CONNECTION_FAILED` | 后端无法连接本机代理；检查 Clash Verge 是否运行、端口是否正确 |
| `LLM_PROXY_TUNNEL_FAILED` | 代理拒绝 HTTPS CONNECT 隧道；检查 HTTP/Mixed 端口、认证及代理规则 |
| `LLM_TLS_FAILED` | HTTPS 证书或握手失败；检查平台证书、地址及代理，不要关闭证书验证 |
| `LLM_DNS_FAILED` | 本机解析失败；检查域名、DNS 或受控代理配置。代理内部 DNS 失败也可能体现为隧道错误 |
| `LLM_AUTH_FAILED` | 请求到达上游后遇到认证或权限问题，检查平台 Key、额度和模型权限 |

代理出口不可达或上游响应缓慢也可能表现为超时，不能单凭超时断言 Key 错误。Coding 调用的部分错误类别会带 `CODING_` 前缀。

## 6. 安全边界与服务器部署

这不是“在代理模式仍校验目标真实公网 IP”的实现。启用代理后，**目标 DNS 与网络出口控制委托给配置的可信代理**；应用继续限制精确基础地址、HTTPS、证书验证、重定向、超时和响应体积。

部署者应同时信任代理与允许列表中的平台，不要允许任意用户控制的域名。即使精确域名在允许列表中，它的 DNS 也可能变化；多用户公开部署时，还需要在代理出口或网络层限制私网、云元数据等访问。普通用户不能通过模型配置或连接测试接口设置代理，也不能修改这份部署者允许列表。

不要把整个 Fake-IP 网段当作公网放行，不要关闭 TLS 验证，不要遇到拦截就自动改用另一条网络线路。API Key、代理原始错误和敏感配置不会回显到页面错误中。

`127.0.0.1` 指的是 **运行后端的机器或容器**：

- 当前 Python 后端直接运行在你的 Windows 电脑上，可以连接同机 Clash Verge。
- 云端后端使用服务器自己的网络出口，不能自动连接访问网页用户电脑上的代理。
- Docker 内的 `127.0.0.1` 是容器自身；本版不支持直接填 `host.docker.internal` 或远程代理。不要照搬 Windows 本地示例到容器；容器出站方案需单独设计。

## 7. 改动位置与检查范围

- `backend/app/integrations/endpoint_policy.py`：纯 URL 校验与部署者代理配置校验，不发网络请求。
- `backend/app/integrations/compatible_transport.py`：精确路由选择、HTTP CONNECT 请求、直连 DNS 保护及分类错误。
- `backend/app/config.py`、`backend/.env.example`：配置读取、启动校验和示例。
- `backend/app/integrations/compatible.py`、`backend/app/coding/compatible_model.py`、`backend/app/model_settings/connection_test.py`：三类入口统一传递后端网络配置。
- `frontend/src/components/ModelSettings.vue`：说明平台 Base URL 与本机代理地址的区别。
- `backend/tests/test_model_proxy.py`：离线模拟用例，覆盖配置、Fake-IP 分支、精确匹配、错误脱敏与三个调用入口；不是实际 Clash 网络验证。

开发时仅做静态语法检查，不运行功能测试、不调用真实模型、不修改真实 `.env` 或数据库，也不操作 Git。离线测试源代码已提供，由用户按需运行；实际网络效果仍需上述手动验收。
