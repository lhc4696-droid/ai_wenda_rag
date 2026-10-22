# 知言 AI 移动端快速启动指南

## 🚀 5 分钟快速开始

### 方案选择建议

| 你的情况 | 推荐方案 | 理由 |
|---------|---------|------|
| 只需要 Android APK | **原生 Android** | 开发效率最高，生态最成熟 |
| 需要微信小程序 | **微信小程序** | 微信生态，无需安装 |
| 需要 Android + iOS | **Kotlin Multiplatform** | 代码复用率 70%+ |
| 需要多端（含 H5） | **uni-app** | 一次开发，多端部署 |

**我的建议：先做 Android 原生 → 再做微信小程序**

---

## 📱 方案一：Android 原生开发（推荐首选）

### 准备工作

#### 1. 安装开发环境

```bash
# 下载 Android Studio（推荐最新稳定版）
https://developer.android.com/studio

# 安装后需要的 SDK 组件：
- Android SDK Platform 34
- Android SDK Build-Tools 34.0.0
- Android SDK Platform-Tools
- Android Emulator
```

#### 2. 创建新项目

**使用 Android Studio：**
```
File → New → New Project
选择：Empty Activity (Compose)
Language: Kotlin
Minimum SDK: API 24 (Android 7.0)
Build configuration language: Kotlin DSL
```

#### 3. 配置 build.gradle.kts

```kotlin
// 项目根目录 build.gradle.kts
plugins {
    id("com.android.application") version "8.2.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.22" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "1.9.22" apply false
    id("com.google.dagger.hilt.android") version "2.50" apply false
    id("com.google.devtools.ksp") version "1.9.22-1.0.17" apply false
}

// app/build.gradle.kts
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.dagger.hilt.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.zhiyan.ai"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.zhiyan.ai"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"

        // 配置后端地址
        buildConfigField("String", "BASE_URL", "\"https://your-domain.com\"")
        // 开发环境
        // buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8000\"") // Android 模拟器访问本机
    }

    buildTypes {
        debug {
            buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8000\"")
        }
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            buildConfigField("String", "BASE_URL", "\"https://your-domain.com\"")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.8"
    }
}

dependencies {
    // Compose BOM (Bill of Materials) 统一版本管理
    val composeBom = platform("androidx.compose:compose-bom:2024.02.00")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    // Compose 核心
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // Lifecycle
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    // Ktor 网络库（推荐，纯 Kotlin）
    val ktorVersion = "2.3.8"
    implementation("io.ktor:ktor-client-android:$ktorVersion")
    implementation("io.ktor:ktor-client-content-negotiation:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktorVersion")
    implementation("io.ktor:ktor-client-logging:$ktorVersion")
    implementation("io.ktor:ktor-client-websockets:$ktorVersion")

    // Kotlin 序列化
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")

    // Room 数据库
    val roomVersion = "2.6.1"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // DataStore（轻量数据存储）
    implementation("androidx.datastore:datastore-preferences:1.0.0")

    // Hilt 依赖注入
    implementation("com.google.dagger:hilt-android:2.50")
    ksp("com.google.dagger:hilt-compiler:2.50")
    implementation("androidx.hilt:hilt-navigation-compose:1.1.0")

    // Coil 图片加载
    implementation("io.coil-kt:coil-compose:2.6.0")

    // Markdown 渲染
    implementation("com.halilibo.compose-richtext:richtext-ui:0.17.0")
    implementation("com.halilibo.compose-richtext:richtext-commonmark:0.17.0")

    // 测试
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
```

#### 4. 最小可运行代码

创建 `app/src/main/java/com/zhiyan/MainActivity.kt`：

```kotlin
package com.zhiyan.ai

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ZhiyanTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    SimpleChat()
                }
            }
        }
    }
}

@Composable
fun SimpleChat() {
    var input by remember { mutableStateOf("") }
    var messages by remember { mutableStateOf(listOf<String>()) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text("知言 AI", style = MaterialTheme.typography.headlineMedium)
        Spacer(modifier = Modifier.height(16.dp))
        
        // 消息列表
        Column(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            messages.forEach { message ->
                Card {
                    Text(
                        text = message,
                        modifier = Modifier.padding(12.dp)
                    )
                }
            }
        }
        
        // 输入框
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("输入消息...") }
            )
            Button(
                onClick = {
                    if (input.isNotBlank()) {
                        messages = messages + input
                        input = ""
                    }
                },
                modifier = Modifier.padding(start = 8.dp)
            ) {
                Text("发送")
            }
        }
    }
}

@Composable
fun ZhiyanTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(),
        content = content
    )
}
```

创建 `app/src/main/java/com/zhiyan/ZhiyanApplication.kt`：

```kotlin
package com.zhiyan.ai

import android.app.Application
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class ZhiyanApplication : Application()
```

修改 `app/src/main/AndroidManifest.xml`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

    <application
        android:name=".ZhiyanApplication"
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="知言 AI"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/Theme.Material3.Light"
        android:usesCleartextTraffic="true">
        
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

#### 5. 运行测试

```bash
# 连接真机或启动模拟器
adb devices

# Android Studio 中点击 Run（绿色三角）
# 或命令行：
./gradlew installDebug
```

---

## 🌐 方案二：微信小程序开发

### 准备工作

#### 1. 注册与认证

```
1. 访问 https://mp.weixin.qq.com/
2. 注册小程序账号（需要邮箱 + 手机）
3. 完成主体认证（个人/企业）
4. 获取 AppID
```

#### 2. 安装开发工具

```
下载微信开发者工具：
https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html
```

#### 3. 创建项目

```
打开微信开发者工具
→ 新建小程序项目
→ 填入 AppID
→ 选择不使用云服务
→ 使用 JavaScript 基础模板
```

#### 4. 项目结构

```
zhiyan-miniprogram/
├── pages/
│   ├── index/
│   │   ├── index.js       // 首页逻辑
│   │   ├── index.json     // 页面配置
│   │   ├── index.wxml     // 页面结构
│   │   └── index.wxss     // 页面样式
│   ├── chat/
│   │   ├── chat.js
│   │   ├── chat.json
│   │   ├── chat.wxml
│   │   └── chat.wxss
│   └── login/
│       ├── login.js
│       ├── login.json
│       ├── login.wxml
│       └── login.wxss
├── utils/
│   ├── api.js             // API 封装
│   └── util.js            // 工具函数
├── app.js                 // 小程序入口
├── app.json               // 全局配置
├── app.wxss               // 全局样式
└── project.config.json    // 项目配置
```

#### 5. 核心配置文件

**app.json**

```json
{
  "pages": [
    "pages/index/index",
    "pages/chat/chat",
    "pages/login/login"
  ],
  "window": {
    "backgroundTextStyle": "light",
    "navigationBarBackgroundColor": "#fff",
    "navigationBarTitleText": "知言 AI",
    "navigationBarTextStyle": "black"
  },
  "tabBar": {
    "list": [
      {
        "pagePath": "pages/index/index",
        "text": "对话",
        "iconPath": "images/chat.png",
        "selectedIconPath": "images/chat-active.png"
      },
      {
        "pagePath": "pages/login/login",
        "text": "我的",
        "iconPath": "images/user.png",
        "selectedIconPath": "images/user-active.png"
      }
    ]
  },
  "sitemapLocation": "sitemap.json"
}
```

**utils/api.js**

```javascript
const BASE_URL = 'https://your-domain.com'

// Cookie 管理（关键！）
let sessionCookie = wx.getStorageSync('session_cookie') || ''

const request = (url, options = {}) => {
  return new Promise((resolve, reject) => {
    wx.showLoading({ title: '加载中...' })
    
    wx.request({
      url: BASE_URL + url,
      method: options.method || 'GET',
      data: options.data,
      header: {
        'Content-Type': 'application/json',
        'Cookie': sessionCookie,
        ...options.header
      },
      success: (res) => {
        wx.hideLoading()
        
        // 保存 Cookie（重要！）
        const setCookie = res.header['Set-Cookie'] || res.header['set-cookie']
        if (setCookie) {
          sessionCookie = setCookie.split(';')[0]
          wx.setStorageSync('session_cookie', sessionCookie)
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else if (res.statusCode === 401) {
          wx.removeStorageSync('session_cookie')
          wx.redirectTo({ url: '/pages/login/login' })
          reject(new Error('登录已过期'))
        } else {
          wx.showToast({
            title: res.data.detail || '请求失败',
            icon: 'none'
          })
          reject(new Error(res.data.detail))
        }
      },
      fail: (err) => {
        wx.hideLoading()
        wx.showToast({
          title: '网络错误',
          icon: 'none'
        })
        reject(err)
      }
    })
  })
}

module.exports = {
  // 认证
  login: (username, password) => 
    request('/api/auth/login', {
      method: 'POST',
      data: { username, password }
    }),
  
  // 对话列表
  getConversations: (cursor) =>
    request(`/api/conversations?limit=20${cursor ? '&cursor=' + cursor : ''}`),
  
  // 发送消息
  sendMessage: (conversationId, content, messageId, sequence) =>
    request(`/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      data: {
        content,
        client_message_id: messageId,
        expected_last_sequence: sequence,
        retry: false
      }
    })
}
```

**pages/chat/chat.wxml**

```xml
<view class="container">
  <scroll-view 
    class="message-list" 
    scroll-y 
    scroll-into-view="{{scrollToView}}"
    scroll-with-animation="{{true}}"
  >
    <block wx:for="{{messages}}" wx:key="id">
      <view id="msg-{{item.id}}" class="message-item {{item.role}}">
        <view class="message-bubble">
          <text>{{item.content}}</text>
        </view>
      </view>
    </block>
    
    <view wx:if="{{loading}}" class="loading">
      <text>正在思考...</text>
    </view>
  </scroll-view>

  <view class="input-bar">
    <textarea 
      class="input" 
      value="{{inputValue}}"
      placeholder="输入消息..."
      bindinput="onInput"
      maxlength="20000"
      auto-height
    />
    <button 
      class="send-btn" 
      type="primary" 
      size="mini"
      disabled="{{!inputValue || loading}}"
      bindtap="sendMessage"
    >
      发送
    </button>
  </view>
</view>
```

**pages/chat/chat.js**

```javascript
const api = require('../../utils/api')

Page({
  data: {
    conversationId: '',
    messages: [],
    inputValue: '',
    loading: false,
    scrollToView: ''
  },

  onLoad(options) {
    this.setData({
      conversationId: options.id || this.generateUUID()
    })
    this.loadMessages()
  },

  loadMessages() {
    api.getMessages(this.data.conversationId)
      .then(res => {
        this.setData({
          messages: res.items.reverse()
        })
        this.scrollToBottom()
      })
      .catch(err => {
        console.error('加载消息失败', err)
      })
  },

  onInput(e) {
    this.setData({
      inputValue: e.detail.value
    })
  },

  sendMessage() {
    const { inputValue, conversationId, messages } = this.data
    if (!inputValue.trim()) return

    const messageId = this.generateUUID()
    const userMessage = {
      id: Date.now(),
      role: 'user',
      content: inputValue
    }

    this.setData({
      messages: [...messages, userMessage],
      inputValue: '',
      loading: true
    })
    this.scrollToBottom()

    api.sendMessage(conversationId, inputValue, messageId, messages.length)
      .then(res => {
        const assistantMessage = {
          id: res.assistant_message.id,
          role: 'assistant',
          content: res.assistant_message.content
        }
        this.setData({
          messages: [...this.data.messages, assistantMessage],
          loading: false
        })
        this.scrollToBottom()
      })
      .catch(err => {
        this.setData({ loading: false })
        wx.showToast({
          title: '发送失败',
          icon: 'none'
        })
      })
  },

  scrollToBottom() {
    const lastMsg = this.data.messages[this.data.messages.length - 1]
    if (lastMsg) {
      this.setData({
        scrollToView: `msg-${lastMsg.id}`
      })
    }
  },

  generateUUID() {
    return 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'.replace(/x/g, () => 
      Math.floor(Math.random() * 16).toString(16)
    )
  }
})
```

**pages/chat/chat.wxss**

```css
.container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background-color: #f5f5f5;
}

.message-list {
  flex: 1;
  padding: 20rpx;
}

.message-item {
  margin-bottom: 20rpx;
  display: flex;
}

.message-item.user {
  justify-content: flex-end;
}

.message-item.assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 70%;
  padding: 20rpx;
  border-radius: 12rpx;
  word-wrap: break-word;
}

.message-item.user .message-bubble {
  background-color: #007AFF;
  color: white;
}

.message-item.assistant .message-bubble {
  background-color: white;
  color: #333;
}

.input-bar {
  display: flex;
  align-items: flex-end;
  padding: 20rpx;
  background-color: white;
  border-top: 1px solid #eee;
}

.input {
  flex: 1;
  min-height: 80rpx;
  max-height: 200rpx;
  padding: 10rpx;
  border: 1px solid #ddd;
  border-radius: 8rpx;
  margin-right: 20rpx;
}

.send-btn {
  flex-shrink: 0;
}

.loading {
  text-align: center;
  color: #999;
  padding: 20rpx;
}
```

#### 6. 调试与发布

```bash
# 本地调试
1. 点击"编译"按钮
2. 在模拟器或真机预览

# 上传代码
1. 点击"上传"
2. 填写版本号和备注

# 提交审核
1. 登录小程序后台
2. 在"版本管理"中提交审核
3. 等待审核通过（1-7天）
4. 审核通过后点击"发布"
```

---

## ⚠️ 常见问题与解决

### Android 问题

**Q1: 模拟器访问不到本机后端**
```kotlin
// 使用 10.0.2.2 代替 localhost
buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8000\"")
```

**Q2: Cookie 不生效**
```kotlin
// 确保安装了 HttpCookies 插件
install(HttpCookies) {
    storage = AcceptAllCookiesStorage()
}
```

**Q3: Compose 预览不显示**
```kotlin
// 添加 @Preview 注解
@Preview(showBackground = true)
@Composable
fun ChatPreview() {
    SimpleChat()
}
```

### 小程序问题

**Q1: 跨域问题**
```
在小程序后台配置服务器域名：
开发 → 开发管理 → 服务器域名
添加 request 合法域名（必须是 HTTPS）
```

**Q2: Cookie 不生效**
```javascript
// 小程序需要手动管理 Cookie
const setCookie = res.header['Set-Cookie']
wx.setStorageSync('session_cookie', setCookie)
```

**Q3: 真机调试网络失败**
```
1. 确保后端已部署 HTTPS
2. 在小程序后台配置域名白名单
3. 开启"不校验合法域名"（仅开发时）
```

---

## 📦 后端配置清单

### 1. CORS 配置

```python
# backend/app/config.py
CORS_ORIGINS = [
    "http://127.0.0.1:5173",  # 前端开发
    "http://localhost:5173",
    "https://your-domain.com",  # 生产环境
    # 注意：移动端不需要特殊 CORS 配置
]
```

### 2. HTTPS 部署（必需！）

```bash
# 使用 Let's Encrypt 免费证书
sudo apt install certbot
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
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3. Cookie 安全配置

```python
# backend/app/config.py
# 生产环境必须设置为 True
SESSION_COOKIE_SECURE = True  # 仅 HTTPS
SESSION_COOKIE_HTTPONLY = True  # 防止 XSS
SESSION_COOKIE_SAMESITE = "lax"  # 防止 CSRF
```

---

## 🎯 下一步行动计划

### Week 1: 环境搭建
- [ ] 安装 Android Studio
- [ ] 创建测试项目
- [ ] 运行 Hello World

### Week 2-3: 核心功能
- [ ] 登录注册界面
- [ ] 网络层封装
- [ ] 聊天界面

### Week 4: 测试优化
- [ ] 真机测试
- [ ] 性能优化
- [ ] 打包发布

---

## 📚 学习资源

### Android
- [官方 Compose 教程](https://developer.android.com/jetpack/compose/tutorial)
- [Kotlin 协程实战](https://kotlinlang.org/docs/coroutines-basics.html)
- [Ktor 客户端文档](https://ktor.io/docs/getting-started-ktor-client.html)

### 微信小程序
- [官方开发文档](https://developers.weixin.qq.com/miniprogram/dev/framework/)
- [API 参考](https://developers.weixin.qq.com/miniprogram/dev/api/)

---

**有任何问题随时问我！建议先从 Android 简单界面开始，一步步实现功能。**
