# 知言 AI - Kotlin 移动端开发总结

## 📱 方案概述

### 推荐技术栈选择

```
优先级 1: Android 原生 (Kotlin + Jetpack Compose)
  ↓ 开发周期: 6-7 周
  ↓ 适合: 想快速上线 Android APK

优先级 2: 微信小程序
  ↓ 开发周期: 3-4 周
  ↓ 适合: 微信生态用户，无需安装

优先级 3: Kotlin Multiplatform (KMP)
  ↓ 开发周期: 8-10 周
  ↓ 适合: 需要 Android + iOS 双端
```

---

## ✅ 已准备的资源

### 文档清单
- ✅ [kotlin-mobile-implementation.md](kotlin-mobile-implementation.md) - 完整技术实施方案
- ✅ [mobile-quick-start.md](mobile-quick-start.md) - 5分钟快速启动
- ✅ [../backend/app/chat/streaming.py](../backend/app/chat/streaming.py) - 流式响应接口

### 核心代码模板
- ✅ Android 项目配置 (build.gradle.kts)
- ✅ 网络层封装 (Ktor + kotlinx.serialization)
- ✅ 数据库设计 (Room)
- ✅ UI 组件 (Jetpack Compose)
- ✅ 微信小程序完整代码
- ✅ Cookie 认证方案

---

## 🎯 Android 开发路线图

### Phase 1: 基础框架 (Week 1-2)

**目标：能运行的登录注册界面**

```kotlin
// 任务清单
□ 安装 Android Studio Hedgehog (2023.1.1+)
□ 创建 Empty Compose Activity 项目
□ 配置 build.gradle.kts 依赖
□ 实现网络层 (Ktor Client + Cookie 管理)
□ 实现登录注册 UI
□ 测试本地后端连接 (10.0.2.2:8000)

// 验收标准
✓ 可以注册新账号
✓ 可以登录并保持会话
✓ Cookie 自动管理
✓ 错误提示清晰
```

**关键代码：**

```kotlin
// NetworkModule.kt
@Provides
@Singleton
fun provideHttpClient(): HttpClient {
    return HttpClient(Android) {
        install(ContentNegotiation) {
            json(Json { ignoreUnknownKeys = true })
        }
        install(HttpCookies) {
            storage = AcceptAllCookiesStorage()
        }
        install(DefaultRequest) {
            url("http://10.0.2.2:8000") // 模拟器访问本机
        }
    }
}
```

### Phase 2: 聊天核心 (Week 3-4)

**目标：可以发送消息并显示回复**

```kotlin
// 任务清单
□ 实现聊天界面布局 (LazyColumn + TextField)
□ 接入 /api/conversations 接口
□ 实现消息发送与接收
□ Markdown 渲染 (compose-richtext)
□ 本地缓存 (Room Database)
□ 消息列表自动滚动

// 验收标准
✓ 发送消息成功
✓ 显示 AI 回复
✓ Markdown 正确渲染
✓ 支持代码高亮
✓ 网络断开后可查看历史
```

**UI 效果：**

```
┌──────────────────────────┐
│    知言 AI          ⋮    │ <- TopBar
├──────────────────────────┤
│                          │
│  ┌────────────────┐      │
│  │ 你好           │ 我   │ <- 用户消息
│  └────────────────┘      │
│                          │
│      ┌─────────────────┐ │
│  AI  │ 你好！我是知言  │ │ <- AI 消息
│      │ 有什么可以帮你？│ │
│      └─────────────────┘ │
│                          │
│  [正在输入...]           │ <- Loading
├──────────────────────────┤
│ [输入消息...   ] [发送]  │ <- Input
└──────────────────────────┘
```

### Phase 3: 增强功能 (Week 5-6)

**目标：完整的聊天应用**

```kotlin
// 任务清单
□ 对话历史列表
□ 新建/切换/删除对话
□ 图片附件上传
□ 文档附件支持
□ 流式响应 (SSE)
□ 设置页面 (模型配置)

// 验收标准
✓ 可以管理多个对话
✓ 可以上传图片询问
✓ 流式输出逐字显示
✓ 可以修改 API 配置
```

### Phase 4: 测试发布 (Week 7)

```kotlin
// 任务清单
□ 真机测试 (3+ 设备)
□ 性能优化 (Compose 重组)
□ 内存泄漏检查 (LeakCanary)
□ 签名配置 (keystore)
□ 混淆配置 (ProGuard)
□ 生成 Release APK
□ 应用商店发布 (可选)

// 验收标准
✓ 无崩溃
✓ 启动速度 < 2s
✓ 内存占用 < 200MB
✓ APK 大小 < 30MB
```

---

## 🌐 微信小程序路线图

### Phase 1: 基础框架 (Week 1)

```javascript
// 任务清单
□ 注册小程序账号并认证
□ 配置服务器域名 (HTTPS)
□ 实现 Cookie 手动管理
□ 登录注册页面
□ 测试接口连通性

// 关键代码
const request = (url, options = {}) => {
  const sessionCookie = wx.getStorageSync('session_cookie')
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + url,
      header: { 'Cookie': sessionCookie },
      success: (res) => {
        // 保存 Cookie
        const setCookie = res.header['Set-Cookie']
        if (setCookie) {
          wx.setStorageSync('session_cookie', setCookie.split(';')[0])
        }
        resolve(res.data)
      }
    })
  })
}
```

### Phase 2-3: 核心功能 (Week 2-3)

```javascript
// 任务清单
□ 聊天界面开发
□ 消息发送与接收
□ 对话历史管理
□ Markdown 渲染 (towxml)
□ 图片上传

// 注意事项
⚠️ 小程序必须 HTTPS
⚠️ 域名需在后台配置白名单
⚠️ 图片上传需使用 wx.uploadFile
⚠️ 长连接需使用 WebSocket
```

### Phase 4: 提审发布 (Week 4)

```
审核要点：
✓ 隐私政策完整
✓ 用户协议清晰
✓ 功能描述准确
✓ 截图符合规范
✓ 不涉及敏感内容

审核周期：1-7 天
```

---

## 🔧 后端改造清单

### 必需改动

#### 1. HTTPS 部署 (强制)

```bash
# 使用 Let's Encrypt 免费证书
sudo certbot certonly --standalone -d your-domain.com

# Nginx 配置
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

#### 2. Cookie 安全配置

```python
# backend/app/config.py
SESSION_COOKIE_SECURE = True  # HTTPS only
SESSION_COOKIE_HTTPONLY = True  # 防 XSS
SESSION_COOKIE_SAMESITE = "lax"  # 防 CSRF
```

#### 3. 流式响应接口 (可选，提升体验)

```python
# 已创建 backend/app/chat/streaming.py
# 需要在 main.py 中注册路由

# backend/app/main.py
from app.chat.streaming import router as streaming_router
application.include_router(streaming_router)
```

### 建议改动

#### 4. 移动端专用接口

```python
# backend/app/mobile/routes.py
@router.get("/api/mobile/config")
async def get_mobile_config():
    """移动端配置"""
    return {
        "version": "1.0.0",
        "min_version": "1.0.0",
        "features": {
            "chat": True,
            "attachments": True,
            "coding": False  # 移动端不支持
        }
    }
```

#### 5. 图片压缩优化

```python
from PIL import Image

@router.post("/api/attachments/mobile")
async def upload_mobile_attachment(file: UploadFile):
    """移动端优化的附件上传，自动压缩图片"""
    if file.content_type.startswith('image/'):
        img = Image.open(file.file)
        # 限制最大尺寸
        img.thumbnail((1920, 1920))
        # 保存压缩后的图片
```

---

## 💰 成本估算

### 开发成本

| 项目 | 时间 | 说明 |
|------|------|------|
| Android 原生 | 6-7周 | 一个开发者全职 |
| 微信小程序 | 3-4周 | 复用 Android 逻辑 |
| iOS (可选) | +4-6周 | 需要 macOS + Xcode |

### 服务器成本

```
基础版（100 DAU）：
- 云服务器: ¥100/月 (2核4G)
- 域名: ¥50/年
- SSL 证书: 免费 (Let's Encrypt)
- CDN (可选): ¥50/月

总计: ~¥150/月
```

### 第三方服务

```
- 阿里云百炼 API: 按量计费
- 对象存储 OSS: ¥0.12/GB/月
- 短信验证码: ¥0.045/条 (可选)
```

---

## 📊 技术对比

### Android vs 小程序 vs KMP

| 维度 | Android 原生 | 微信小程序 | KMP |
|------|-------------|-----------|-----|
| **开发难度** | ⭐⭐⭐ 中等 | ⭐⭐ 简单 | ⭐⭐⭐⭐ 较难 |
| **性能** | ⭐⭐⭐⭐⭐ 最优 | ⭐⭐⭐ 良好 | ⭐⭐⭐⭐ 优秀 |
| **开发效率** | ⭐⭐⭐⭐ 高 | ⭐⭐⭐⭐⭐ 最高 | ⭐⭐⭐ 中等 |
| **用户覆盖** | 仅 Android | 微信用户 | Android + iOS |
| **分发成本** | 应用商店 | 无需安装 | 双端商店 |
| **代码复用** | 0% | 0% | 70-80% |
| **学习曲线** | 中等 | 平缓 | 陡峭 |

### 推荐策略

```
初创团队/个人:
  → 先做 Android 原生 (验证市场)
  → 再做微信小程序 (扩大覆盖)

企业团队:
  → 同时开发 Android + 小程序
  → 或使用 KMP 统一双端

有 iOS 需求:
  → Kotlin Multiplatform
  → 或 Flutter (跨平台备选)
```

---

## 🚀 立即开始

### 今天就能做的事

**选择 Android：**

```bash
# 1. 下载 Android Studio (2-3 小时)
https://developer.android.com/studio

# 2. 创建第一个项目 (30 分钟)
按照 mobile-quick-start.md 操作

# 3. 运行 Hello World (10 分钟)
点击绿色三角运行

# 4. 复制聊天界面代码 (1 小时)
从文档复制 SimpleChat.kt

# 今日目标: 看到界面在模拟器运行 ✓
```

**选择小程序：**

```bash
# 1. 注册小程序账号 (1 小时)
https://mp.weixin.qq.com/

# 2. 下载开发工具 (30 分钟)
https://developers.weixin.qq.com/miniprogram/dev/devtools/

# 3. 创建测试项目 (30 分钟)
选择 JavaScript 模板

# 4. 修改首页代码 (1 小时)
复制 pages/index/index.js

# 今日目标: 在开发工具中看到页面 ✓
```

---

## 📞 遇到问题？

### Android 常见问题

**Q: 模拟器太慢？**
```
A: 使用 Android 真机调试
   或安装 Intel HAXM 加速
```

**Q: Gradle 同步失败？**
```
A: 检查网络连接
   配置 Maven 阿里云镜像
   删除 .gradle 重新同步
```

**Q: Cookie 不生效？**
```
A: 检查 HttpCookies 插件配置
   确认后端 Set-Cookie 返回
```

### 小程序常见问题

**Q: request:fail url not in domain list？**
```
A: 在小程序后台配置服务器域名
   或勾选"不校验合法域名"(仅开发)
```

**Q: Cookie 丢失？**
```
A: 小程序需要手动管理 Cookie
   从 res.header['Set-Cookie'] 提取
   每次请求手动带上
```

**Q: 真机预览失败？**
```
A: 确保后端是 HTTPS
   检查手机与电脑在同一网络
```

---

## 🎓 学习资源

### Android
- [官方 Compose 教程](https://developer.android.com/jetpack/compose/tutorial)
- [Ktor Client 文档](https://ktor.io/docs/client.html)
- [Kotlin 协程指南](https://kotlinlang.org/docs/coroutines-guide.html)

### 小程序
- [官方开发文档](https://developers.weixin.qq.com/miniprogram/dev/framework/)
- [Vant Weapp 组件库](https://vant-contrib.gitee.io/vant-weapp/)

### 视频教程
- [Jetpack Compose 从入门到实战](https://www.bilibili.com/video/BV1xx4y1y7bJ)
- [微信小程序开发教程](https://www.bilibili.com/video/BV1nE41117BQ)

---

## 📈 后续扩展

### 短期 (1-3 个月)
- [ ] 语音输入支持
- [ ] 深色模式
- [ ] 消息搜索
- [ ] 分享功能

### 中期 (3-6 个月)
- [ ] iOS 版本 (SwiftUI 或 KMP)
- [ ] 离线模式
- [ ] 智能推送
- [ ] 用户反馈系统

### 长期 (6-12 个月)
- [ ] 平板适配
- [ ] 桌面应用 (Compose Desktop)
- [ ] 浏览器插件
- [ ] 智能手表版

---

## ✅ 最终检查清单

### 开发前
- [ ] 确认后端已部署 HTTPS
- [ ] 准备好开发设备（Android 手机或模拟器）
- [ ] 安装必需的开发工具
- [ ] 阅读完快速启动文档

### 开发中
- [ ] 每天提交代码到 Git
- [ ] 每周真机测试一次
- [ ] 记录遇到的问题和解决方案
- [ ] 保持后端接口文档更新

### 发布前
- [ ] 完整功能测试
- [ ] 性能测试和优化
- [ ] 准备应用截图和描述
- [ ] 配置签名和混淆
- [ ] 生成 Release 版本

---

**现在就开始吧！选择一个方案，打开开发工具，跑起第一个 Hello World！**

有任何问题随时问我 🚀
