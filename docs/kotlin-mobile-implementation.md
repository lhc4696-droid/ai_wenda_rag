# 知言 AI - Kotlin 移动端实现方案

## 一、Android 原生开发

### 1.1 Gradle 配置 (build.gradle.kts)

```kotlin
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

        buildConfigField("String", "BASE_URL", "\"http://127.0.0.1:8000\"")
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
    // Compose
    val composeBom = platform("androidx.compose:compose-bom:2024.01.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.navigation:navigation-compose:2.7.6")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // Lifecycle & ViewModel
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    // Networking
    implementation("io.ktor:ktor-client-android:2.3.7")
    implementation("io.ktor:ktor-client-content-negotiation:2.3.7")
    implementation("io.ktor:ktor-serialization-kotlinx-json:2.3.7")
    implementation("io.ktor:ktor-client-logging:2.3.7")
    implementation("io.ktor:ktor-client-websockets:2.3.7")

    // Serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.2")

    // Database
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    // DataStore
    implementation("androidx.datastore:datastore-preferences:1.0.0")

    // Dependency Injection
    implementation("com.google.dagger:hilt-android:2.50")
    ksp("com.google.dagger:hilt-compiler:2.50")
    implementation("androidx.hilt:hilt-navigation-compose:1.1.0")

    // Image Loading
    implementation("io.coil-kt:coil-compose:2.5.0")

    // Markdown
    implementation("com.halilibo.compose-richtext:richtext-ui:0.17.0")
    implementation("com.halilibo.compose-richtext:richtext-commonmark:0.17.0")

    // Testing
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
```

### 1.2 核心代码实现

#### AuthApi.kt - 认证接口

```kotlin
package com.zhiyan.data.remote.api

import io.ktor.client.*
import io.ktor.client.call.*
import io.ktor.client.request.*
import io.ktor.http.*
import kotlinx.serialization.Serializable

interface AuthApi {
    suspend fun sendEmailCode(email: String, purpose: String): EmailCodeResponse
    suspend fun register(username: String, password: String, email: String, code: String): AuthResponse
    suspend fun login(username: String, password: String): AuthResponse
    suspend fun getMe(): UserResponse
    suspend fun logout()
    suspend fun changePassword(currentPassword: String, newPassword: String)
}

class AuthApiImpl(private val client: HttpClient) : AuthApi {
    
    override suspend fun sendEmailCode(email: String, purpose: String): EmailCodeResponse {
        return client.post("/api/auth/email-code") {
            contentType(ContentType.Application.Json)
            setBody(SendEmailCodeRequest(email, purpose))
        }.body()
    }

    override suspend fun register(
        username: String, 
        password: String, 
        email: String, 
        code: String
    ): AuthResponse {
        return client.post("/api/auth/register") {
            contentType(ContentType.Application.Json)
            setBody(RegisterRequest(username, password, email, code))
        }.body()
    }

    override suspend fun login(username: String, password: String): AuthResponse {
        return client.post("/api/auth/login") {
            contentType(ContentType.Application.Json)
            setBody(LoginRequest(username, password))
        }.body()
    }

    override suspend fun getMe(): UserResponse {
        return client.get("/api/auth/me").body()
    }

    override suspend fun logout() {
        client.post("/api/auth/logout")
    }

    override suspend fun changePassword(currentPassword: String, newPassword: String) {
        client.post("/api/auth/password/change") {
            contentType(ContentType.Application.Json)
            setBody(ChangePasswordRequest(currentPassword, newPassword))
        }
    }
}

// DTOs
@Serializable
data class SendEmailCodeRequest(val email: String, val purpose: String)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
    val email: String,
    val code: String
)

@Serializable
data class LoginRequest(val username: String, val password: String)

@Serializable
data class ChangePasswordRequest(
    val current_password: String,
    val new_password: String
)

@Serializable
data class EmailCodeResponse(
    val message: String,
    val retry_after: Int,
    val expires_in: Int
)

@Serializable
data class AuthResponse(val user: UserResponse)

@Serializable
data class UserResponse(
    val id: Int,
    val username: String,
    val email: String? = null
)
```

#### ConversationApi.kt - 对话接口

```kotlin
package com.zhiyan.data.remote.api

import io.ktor.client.*
import io.ktor.client.call.*
import io.ktor.client.request.*
import io.ktor.http.*
import kotlinx.serialization.Serializable

interface ConversationApi {
    suspend fun createConversation(id: String): ConversationResponse
    suspend fun listConversations(cursor: String? = null): ConversationPage
    suspend fun getMessages(conversationId: String, before: Int? = null): MessagePage
    suspend fun sendMessage(conversationId: String, request: SendMessageRequest): TurnResponse
    suspend fun renameConversation(conversationId: String, title: String): ConversationResponse
    suspend fun deleteConversation(conversationId: String)
}

class ConversationApiImpl(private val client: HttpClient) : ConversationApi {
    
    override suspend fun createConversation(id: String): ConversationResponse {
        return client.post("/api/conversations") {
            contentType(ContentType.Application.Json)
            setBody(CreateConversationRequest(id))
        }.body()
    }

    override suspend fun listConversations(cursor: String?): ConversationPage {
        return client.get("/api/conversations") {
            parameter("limit", 20)
            cursor?.let { parameter("cursor", it) }
        }.body()
    }

    override suspend fun getMessages(conversationId: String, before: Int?): MessagePage {
        return client.get("/api/conversations/$conversationId/messages") {
            parameter("limit", 50)
            before?.let { parameter("before", it) }
        }.body()
    }

    override suspend fun sendMessage(
        conversationId: String,
        request: SendMessageRequest
    ): TurnResponse {
        return client.post("/api/conversations/$conversationId/messages") {
            contentType(ContentType.Application.Json)
            setBody(request)
            timeout {
                requestTimeoutMillis = 120_000 // 2分钟超时
            }
        }.body()
    }

    override suspend fun renameConversation(conversationId: String, title: String): ConversationResponse {
        return client.patch("/api/conversations/$conversationId") {
            contentType(ContentType.Application.Json)
            setBody(RenameRequest(title))
        }.body()
    }

    override suspend fun deleteConversation(conversationId: String) {
        client.delete("/api/conversations/$conversationId")
    }
}

// DTOs
@Serializable
data class CreateConversationRequest(val id: String)

@Serializable
data class SendMessageRequest(
    val content: String,
    val client_message_id: String,
    val expected_last_sequence: Int,
    val retry: Boolean = false,
    val attachment_ids: List<String> = emptyList(),
    val source_mode: String = "chat" // "chat" | "documents"
)

@Serializable
data class RenameRequest(val title: String)

@Serializable
data class ConversationResponse(
    val id: String,
    val title: String,
    val title_custom: Boolean,
    val created_at: Long,
    val updated_at: Long
)

@Serializable
data class ConversationPage(
    val items: List<ConversationResponse>,
    val next_cursor: String? = null
)

@Serializable
data class MessageResponse(
    val id: Int,
    val conversation_id: String,
    val role: String, // "user" | "assistant"
    val content: String,
    val sequence: Int,
    val created_at: Long,
    val attachments: List<AttachmentResponse> = emptyList()
)

@Serializable
data class MessagePage(
    val items: List<MessageResponse>,
    val has_more: Boolean
)

@Serializable
data class TurnResponse(
    val conversation: ConversationResponse,
    val user_message: MessageResponse,
    val assistant_message: MessageResponse,
    val replayed: Boolean
)

@Serializable
data class AttachmentResponse(
    val id: String,
    val filename: String,
    val content_type: String,
    val size: Int,
    val state: String
)
```

#### NetworkModule.kt - 网络依赖注入

```kotlin
package com.zhiyan.di

import android.content.Context
import com.zhiyan.BuildConfig
import com.zhiyan.data.local.PreferencesManager
import com.zhiyan.data.remote.api.*
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import io.ktor.client.*
import io.ktor.client.engine.android.*
import io.ktor.client.plugins.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.cookies.*
import io.ktor.client.plugins.logging.*
import io.ktor.http.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.serialization.json.Json
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    fun provideJson(): Json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        encodeDefaults = true
    }

    @Provides
    @Singleton
    fun provideHttpClient(
        json: Json,
        @ApplicationContext context: Context,
        preferencesManager: PreferencesManager
    ): HttpClient {
        return HttpClient(Android) {
            install(ContentNegotiation) {
                json(json)
            }

            install(Logging) {
                logger = Logger.ANDROID
                level = if (BuildConfig.DEBUG) LogLevel.ALL else LogLevel.NONE
            }

            install(HttpCookies) {
                // Cookie 自动管理，模拟浏览器行为
                storage = AcceptAllCookiesStorage()
            }

            install(HttpTimeout) {
                requestTimeoutMillis = 30_000
                connectTimeoutMillis = 10_000
            }

            install(DefaultRequest) {
                url(BuildConfig.BASE_URL)
                headers {
                    append(HttpHeaders.ContentType, ContentType.Application.Json)
                    append(HttpHeaders.Accept, ContentType.Application.Json)
                }
            }

            // 处理 401 自动跳转登录
            install(HttpSend) {
                intercept { request ->
                    val response = execute(request)
                    if (response.status == HttpStatusCode.Unauthorized) {
                        // 触发登录过期事件
                        preferencesManager.clearAuth()
                    }
                    response
                }
            }
        }
    }

    @Provides
    @Singleton
    fun provideAuthApi(client: HttpClient): AuthApi = AuthApiImpl(client)

    @Provides
    @Singleton
    fun provideConversationApi(client: HttpClient): ConversationApi = ConversationApiImpl(client)

    @Provides
    @Singleton
    fun provideAttachmentApi(client: HttpClient): AttachmentApi = AttachmentApiImpl(client)
}
```

#### ChatViewModel.kt - 聊天界面 ViewModel

```kotlin
package com.zhiyan.ui.screen.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.zhiyan.domain.model.Conversation
import com.zhiyan.domain.model.Message
import com.zhiyan.domain.usecase.chat.GetConversationsUseCase
import com.zhiyan.domain.usecase.chat.GetMessagesUseCase
import com.zhiyan.domain.usecase.chat.SendMessageUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.util.*
import javax.inject.Inject

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val getConversationsUseCase: GetConversationsUseCase,
    private val getMessagesUseCase: GetMessagesUseCase,
    private val sendMessageUseCase: SendMessageUseCase
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val _currentConversationId = MutableStateFlow<String?>(null)

    init {
        loadConversations()
    }

    fun loadConversations() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingConversations = true) }
            getConversationsUseCase()
                .onSuccess { conversations ->
                    _uiState.update { 
                        it.copy(
                            conversations = conversations,
                            isLoadingConversations = false
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { 
                        it.copy(
                            error = error.message,
                            isLoadingConversations = false
                        )
                    }
                }
        }
    }

    fun selectConversation(conversationId: String) {
        _currentConversationId.value = conversationId
        loadMessages(conversationId)
    }

    fun createNewConversation() {
        val newId = UUID.randomUUID().toString().replace("-", "")
        _currentConversationId.value = newId
        _uiState.update { 
            it.copy(
                messages = emptyList(),
                currentInput = ""
            )
        }
    }

    private fun loadMessages(conversationId: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingMessages = true) }
            getMessagesUseCase(conversationId)
                .onSuccess { messages ->
                    _uiState.update { 
                        it.copy(
                            messages = messages,
                            isLoadingMessages = false
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { 
                        it.copy(
                            error = error.message,
                            isLoadingMessages = false
                        )
                    }
                }
        }
    }

    fun sendMessage(content: String) {
        val conversationId = _currentConversationId.value ?: return
        val messageId = UUID.randomUUID().toString().replace("-", "")

        viewModelScope.launch {
            _uiState.update { it.copy(isSending = true, currentInput = "") }
            
            sendMessageUseCase(
                conversationId = conversationId,
                content = content,
                messageId = messageId,
                expectedSequence = _uiState.value.messages.size
            )
                .onSuccess { turn ->
                    val updatedMessages = _uiState.value.messages + listOf(
                        turn.userMessage,
                        turn.assistantMessage
                    )
                    _uiState.update { 
                        it.copy(
                            messages = updatedMessages,
                            isSending = false,
                            error = null
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { 
                        it.copy(
                            error = error.message,
                            isSending = false,
                            currentInput = content // 恢复输入内容
                        )
                    }
                }
        }
    }

    fun updateInput(text: String) {
        _uiState.update { it.copy(currentInput = text) }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }
}

data class ChatUiState(
    val conversations: List<Conversation> = emptyList(),
    val messages: List<Message> = emptyList(),
    val currentInput: String = "",
    val isLoadingConversations: Boolean = false,
    val isLoadingMessages: Boolean = false,
    val isSending: Boolean = false,
    val error: String? = null
)
```

#### ChatScreen.kt - 聊天界面 UI

```kotlin
package com.zhiyan.ui.screen.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.halilibo.richtext.markdown.Markdown
import com.halilibo.richtext.ui.RichText

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()

    // 自动滚动到最新消息
    LaunchedEffect(uiState.messages.size) {
        if (uiState.messages.isNotEmpty()) {
            listState.animateScrollToItem(uiState.messages.size - 1)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("知言 AI") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // 消息列表
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(uiState.messages) { message ->
                    MessageBubble(message)
                }

                if (uiState.isSending) {
                    item {
                        LoadingIndicator()
                    }
                }
            }

            // 错误提示
            uiState.error?.let { error ->
                Snackbar(
                    modifier = Modifier.padding(16.dp),
                    action = {
                        TextButton(onClick = { viewModel.clearError() }) {
                            Text("关闭")
                        }
                    }
                ) {
                    Text(error)
                }
            }

            // 输入框
            ChatInput(
                value = uiState.currentInput,
                onValueChange = { viewModel.updateInput(it) },
                onSend = { viewModel.sendMessage(it) },
                enabled = !uiState.isSending,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            )
        }
    }
}

@Composable
fun MessageBubble(message: Message) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (message.role == "user") 
            Arrangement.End else Arrangement.Start
    ) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = if (message.role == "user")
                MaterialTheme.colorScheme.primary
            else
                MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.widthIn(max = 280.dp)
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                if (message.role == "assistant") {
                    // Markdown 渲染
                    RichText(modifier = Modifier.fillMaxWidth()) {
                        Markdown(content = message.content)
                    }
                } else {
                    Text(
                        text = message.content,
                        color = if (message.role == "user")
                            MaterialTheme.colorScheme.onPrimary
                        else
                            MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}

@Composable
fun ChatInput(
    value: String,
    onValueChange: (String) -> Unit,
    onSend: (String) -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.weight(1f),
            placeholder = { Text("输入消息...") },
            enabled = enabled,
            maxLines = 4
        )

        IconButton(
            onClick = { 
                if (value.isNotBlank()) {
                    onSend(value)
                }
            },
            enabled = enabled && value.isNotBlank()
        ) {
            Icon(Icons.Default.Send, contentDescription = "发送")
        }
    }
}

@Composable
fun LoadingIndicator() {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = Alignment.Center
    ) {
        CircularProgressIndicator()
    }
}
```

### 1.3 本地数据库设计

```kotlin
package com.zhiyan.data.local

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "conversations")
data class ConversationEntity(
    @PrimaryKey val id: String,
    val title: String,
    val titleCustom: Boolean,
    val createdAt: Long,
    val updatedAt: Long
)

@Entity(
    tableName = "messages",
    foreignKeys = [
        ForeignKey(
            entity = ConversationEntity::class,
            parentColumns = ["id"],
            childColumns = ["conversationId"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [Index("conversationId")]
)
data class MessageEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val conversationId: String,
    val role: String,
    val content: String,
    val sequence: Int,
    val createdAt: Long
)

@Dao
interface ConversationDao {
    @Query("SELECT * FROM conversations ORDER BY updatedAt DESC")
    fun getAllConversations(): Flow<List<ConversationEntity>>

    @Query("SELECT * FROM conversations WHERE id = :id")
    suspend fun getConversation(id: String): ConversationEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertConversation(conversation: ConversationEntity)

    @Query("DELETE FROM conversations WHERE id = :id")
    suspend fun deleteConversation(id: String)
}

@Dao
interface MessageDao {
    @Query("SELECT * FROM messages WHERE conversationId = :conversationId ORDER BY sequence ASC")
    fun getMessages(conversationId: String): Flow<List<MessageEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertMessages(messages: List<MessageEntity>)

    @Query("DELETE FROM messages WHERE conversationId = :conversationId")
    suspend fun deleteMessages(conversationId: String)
}

@Database(
    entities = [ConversationEntity::class, MessageEntity::class],
    version = 1,
    exportSchema = false
)
abstract class ZhiyanDatabase : RoomDatabase() {
    abstract fun conversationDao(): ConversationDao
    abstract fun messageDao(): MessageDao
}
```

---

## 二、微信小程序开发方案

### 2.1 技术栈

```javascript
// 框架选择
- 原生微信小程序 (推荐，性能最好)
- 或 uni-app (跨平台，可同时生成小程序和 H5)

// 状态管理
- MobX / Pinia

// UI 组件库
- Vant Weapp
- ColorUI

// 网络请求
- wx.request 封装
- WebSocket 支持流式
```

### 2.2 目录结构

```
zhiyan-miniprogram/
├── pages/
│   ├── index/              // 首页（对话列表）
│   ├── chat/               // 聊天页面
│   ├── login/              // 登录注册
│   └── settings/           // 设置页面
├── components/
│   ├── message-bubble/     // 消息气泡
│   ├── markdown-view/      // Markdown 渲染
│   └── input-bar/          // 输入栏
├── utils/
│   ├── api.js              // API 封装
│   ├── auth.js             // 认证管理
│   └── storage.js          // 本地存储
├── app.js
├── app.json
└── app.wxss
```

### 2.3 关键代码

#### utils/api.js

```javascript
const BASE_URL = 'https://your-domain.com'

// Cookie 管理（小程序需要手动管理）
let sessionCookie = wx.getStorageSync('session_cookie') || ''

const request = (url, options = {}) => {
  return new Promise((resolve, reject) => {
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
        // 保存 Cookie
        const setCookie = res.header['Set-Cookie'] || res.header['set-cookie']
        if (setCookie) {
          sessionCookie = setCookie.split(';')[0]
          wx.setStorageSync('session_cookie', sessionCookie)
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else if (res.statusCode === 401) {
          // 登录过期
          wx.removeStorageSync('session_cookie')
          wx.redirectTo({ url: '/pages/login/login' })
          reject(new Error('登录已过期'))
        } else {
          reject(new Error(res.data.message || '请求失败'))
        }
      },
      fail: reject
    })
  })
}

export const authApi = {
  login: (username, password) => 
    request('/api/auth/login', {
      method: 'POST',
      data: { username, password }
    }),
    
  register: (username, password, email, code) =>
    request('/api/auth/register', {
      method: 'POST',
      data: { username, password, email, code }
    }),
    
  getMe: () => request('/api/auth/me')
}

export const chatApi = {
  getConversations: (cursor) =>
    request(`/api/conversations?limit=20${cursor ? '&cursor=' + cursor : ''}`),
    
  getMessages: (conversationId, before) =>
    request(`/api/conversations/${conversationId}/messages?limit=50${before ? '&before=' + before : ''}`),
    
  sendMessage: (conversationId, data) =>
    request(`/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      data
    })
}
```

#### pages/chat/chat.wxml

```xml
<view class="container">
  <!-- 消息列表 -->
  <scroll-view 
    class="message-list" 
    scroll-y 
    scroll-into-view="{{scrollToView}}"
    scroll-with-animation
  >
    <view 
      wx:for="{{messages}}" 
      wx:key="id" 
      id="msg-{{item.id}}"
      class="message-item {{item.role}}"
    >
      <view class="message-bubble">
        <text wx:if="{{item.role === 'user'}}">{{item.content}}</text>
        <towxml wx:else wx-if="{{item.markdown}}" nodes="{{item.markdown}}" />
      </view>
    </view>
    
    <view wx:if="{{loading}}" class="loading">
      <van-loading type="spinner" />
    </view>
  </scroll-view>

  <!-- 输入框 -->
  <view class="input-bar">
    <van-field
      value="{{inputValue}}"
      placeholder="输入消息..."
      type="textarea"
      autosize
      maxlength="20000"
      bind:change="onInputChange"
    />
    <van-button 
      type="primary" 
      size="small"
      disabled="{{!inputValue || sending}}"
      bindtap="sendMessage"
    >
      发送
    </van-button>
  </view>
</view>
```

---

## 三、后端适配改造

### 3.1 移动端特有接口需求

```python
# backend/app/mobile/routes.py

from fastapi import APIRouter, Depends
from app.auth.routes import require_user

router = APIRouter(prefix="/api/mobile", tags=["移动端"])

@router.get("/config")
async def get_mobile_config():
    """移动端配置信息"""
    return {
        "version": "1.0.0",
        "min_version": "1.0.0",
        "features": {
            "chat": True,
            "rag": True,
            "coding": False,  # 移动端暂不支持
            "attachments": True
        },
        "limits": {
            "max_message_length": 20000,
            "max_attachments": 3,
            "max_file_size": 10 * 1024 * 1024
        }
    }

@router.post("/feedback")
async def submit_feedback(
    content: str,
    user: UserResponse = Depends(require_user)
):
    """用户反馈"""
    # 保存反馈到数据库或发送邮件
    pass
```

### 3.2 流式响应支持（重要）

```python
# backend/app/chat/streaming.py

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

@router.post("/conversations/{id}/messages/stream")
async def stream_message(
    conversation_id: str,
    data: SendMessage,
    user: UserResponse = Depends(require_user),
    provider: LLMProvider = Depends(get_llm_provider)
):
    async def generate():
        try:
            async for chunk in provider.stream_chat(messages):
                yield f"data: {json.dumps({'type': 'content', 'data': chunk})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
```

---

## 四、开发时间线与优先级

### Phase 1: Android MVP (4-6 周)
**Week 1-2:**
- ✅ 项目初始化与架构搭建
- ✅ 登录注册功能
- ✅ 网络层封装

**Week 3-4:**
- ✅ 聊天界面开发
- ✅ 消息列表与发送
- ✅ 本地缓存

**Week 5-6:**
- ✅ 对话历史管理
- ✅ 附件上传（图片/文档）
- ✅ 测试与优化

### Phase 2: 小程序版本 (3-4 周)
- 复用 Android 的业务逻辑
- 适配微信小程序规范
- 提交审核

### Phase 3: 高级功能 (持续迭代)
- 流式输出
- RAG 文档模式
- 语音输入
- 深色模式
- 离线模式

---

## 五、关键技术难点与解决方案

### 5.1 Cookie 管理

**问题：** 移动端没有浏览器自动 Cookie 管理

**Android 解决方案：**
```kotlin
// Ktor 自带 HttpCookies 插件
install(HttpCookies) {
    storage = AcceptAllCookiesStorage()
}
```

**小程序解决方案：**
```javascript
// 手动管理 Cookie
let sessionCookie = wx.getStorageSync('session_cookie')
header: {
  'Cookie': sessionCookie
}
```

### 5.2 Markdown 渲染

**Android：** 
- `compose-richtext` 库
- 支持代码高亮

**小程序：**
- `towxml` 库
- 或 `wxParse`

### 5.3 图片上传

**Android：**
```kotlin
suspend fun uploadAttachment(uri: Uri): Result<AttachmentResponse> {
    val file = uriToFile(uri)
    return client.submitFormWithBinaryData(
        url = "/api/attachments",
        formData = formData {
            append("file", file.readBytes(), Headers.build {
                append(HttpHeaders.ContentType, "image/jpeg")
                append(HttpHeaders.ContentDisposition, "filename=${file.name}")
            })
        }
    )
}
```

**小程序：**
```javascript
wx.chooseImage({
  success: (res) => {
    wx.uploadFile({
      url: BASE_URL + '/api/attachments',
      filePath: res.tempFilePaths[0],
      name: 'file',
      header: { 'Cookie': sessionCookie }
    })
  }
})
```

---

## 六、部署与发布

### 6.1 Android 打包

```bash
# Debug 版本
./gradlew assembleDebug

# Release 版本（需要签名）
./gradlew assembleRelease
```

### 6.2 小程序提交

1. 微信开发者工具上传代码
2. 登录小程序后台提交审核
3. 审核通过后发布

### 6.3 后端 HTTPS 部署

移动端必须使用 HTTPS：

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 七、后续扩展方向

1. **iOS 版本** - 使用 SwiftUI 或 Kotlin Multiplatform
2. **桌面应用** - Electron 或 Tauri
3. **浏览器插件** - Chrome/Edge Extension
4. **智能手表** - WearOS / watchOS
5. **语音助手** - 接入小爱/天猫精灵

---

## 附录：完整资源清单

### 学习资源
- [Jetpack Compose 官方教程](https://developer.android.com/jetpack/compose)
- [Kotlin 协程指南](https://kotlinlang.org/docs/coroutines-guide.html)
- [微信小程序开发文档](https://developers.weixin.qq.com/miniprogram/dev/framework/)

### 开源项目参考
- [JetChat](https://github.com/android/compose-samples/tree/main/Jetchat) - Google 官方聊天 Demo
- [ChatGPT-Android](https://github.com/skydoves/chatgpt-android) - ChatGPT Android 客户端
- [Wechat-Miniprogram](https://github.com/wechat-miniprogram) - 微信小程序示例

---

**建议先从 Android 原生开发入手**，成熟后再考虑小程序版本。如需完整代码示例或遇到具体问题，随时告诉我！
