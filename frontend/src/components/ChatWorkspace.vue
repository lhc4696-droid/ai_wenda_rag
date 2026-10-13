<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ChatMessage from './ChatMessage.vue'
import AccountSettings from './AccountSettings.vue'
import AccountMenu from './AccountMenu.vue'
import ConversationDialog from './ConversationDialog.vue'
import AttachmentPicker from './AttachmentPicker.vue'
import AttachmentCard from './AttachmentCard.vue'
import RetrievalChoice from './RetrievalChoice.vue'
import '../attachments.css'
import { checkHealth } from '../api/chat.js'
import { MAX_MESSAGE_LENGTH, useConversations } from '../composables/useConversations.js'

defineProps({
  user: { type: Object, required: true },
  signingOut: { type: Boolean, default: false },
})
const emit = defineEmits(['logout', 'profile-updated', 'password-changed', 'open-coding'])

const {
  messages,
  draft,
  sourceMode,
  routeChoice,
  isLoading,
  error,
  model,
  retrySeconds,
  canSend,
  send,
  chooseRoute,
  cancelRouteChoice,
  setSourceMode,
  retry,
  editFailed,
  reset,
  conversations,
  current,
  nextCursor,
  nextBefore,
  listLoading,
  listError,
  loadError,
  opening,
  refreshing,
  loadingOlder,
  notice,
  submitting,
  initialize,
  loadList,
  openConversation,
  refreshCurrent,
  loadOlder,
  renameConversation,
  deleteConversation,
  dispose,
  draftAttachments,
  attachmentCapabilities,
  attachmentError,
  attachmentBusy,
  loadCapabilities,
  addFiles,
  removeAttachment,
} = useConversations()
const sidebarOpen = ref(false)
const actionDialog = ref(null)
const settingsOpen = ref(false)
const settingsTab = ref('profile')
const textarea = ref(null)
const thread = ref(null)
const health = ref('checking')
const hasMessages = computed(() => messages.value.length > 0)
const title = computed(() => current.value?.title || '新的对话')
const statusLabel = computed(
  () =>
    ({ checking: '正在连接', connected: '服务已连接', disconnected: '服务未连接' })[health.value],
)
const suggestions = [
  {
    icon: 'book',
    title: '理解一个概念',
    subtitle: '把复杂的知识，讲得简单一点',
    prompt: '请用一个生活中的例子，解释什么是大语言模型。',
  },
  {
    icon: 'pen',
    title: '写点有趣的内容',
    subtitle: '给还没成形的想法，一个开头',
    prompt: '帮我写一段关于周末散步的短文，语气自然，有一点画面感。',
  },
  {
    icon: 'layers',
    title: '整理我的思路',
    subtitle: '从一个目标，找到下一步',
    prompt: '我想学习一项新技能，请先问我几个问题，帮我明确目标和学习计划。',
  },
]

async function refreshHealth() {
  if (health.value === 'checking' && healthCheckStarted) return
  healthCheckStarted = true
  health.value = 'checking'
  health.value = (await checkHealth()) ? 'connected' : 'disconnected'
  healthCheckStarted = false
}
let healthCheckStarted = false

async function focusComposer() {
  await nextTick()
  textarea.value?.focus()
}

function startNew() {
  reset()
  sidebarOpen.value = false
  focusComposer()
}

async function selectConversation(id) {
  sidebarOpen.value = false
  await openConversation(id)
  // 切换历史时保留侧栏焦点，避免移动端无意弹出键盘。
}

function showConversationAction(conversation, mode) {
  actionDialog.value = { conversation, mode }
}

let prependingId = null
async function showOlderMessages() {
  if (!thread.value) return
  const element = thread.value
  const previousHeight = element.scrollHeight
  const previousTop = element.scrollTop
  const conversationId = current.value?.id
  prependingId = conversationId
  try {
    const loaded = await loadOlder()
    await nextTick()
    if (loaded && current.value?.id === conversationId && thread.value === element)
      element.scrollTop = previousTop + element.scrollHeight - previousHeight
  } finally {
    if (prependingId === conversationId) prependingId = null
  }
}

function openSettings(tab) {
  if (isLoading.value || opening.value) return
  settingsTab.value = tab
  settingsOpen.value = true
  sidebarOpen.value = false
}

function openCoding() {
  sidebarOpen.value = false
  emit('open-coding')
}

function choosePrompt(prompt) {
  draft.value = prompt
  focusComposer()
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    submit()
  }
}

async function submit() {
  if (!canSend.value) return
  await send()
  if (!routeChoice.value) focusComposer()
}

async function confirmRoute(mode) {
  await chooseRoute(mode)
  focusComposer()
}

async function retryAnswer() {
  await retry()
  focusComposer()
}

function editQuestion() {
  editFailed()
  focusComposer()
}

watch(draft, async () => {
  await nextTick()
  if (textarea.value) {
    textarea.value.style.height = 'auto'
    textarea.value.style.height = `${Math.min(textarea.value.scrollHeight, 180)}px`
  }
})

let displayedConversationId = null
function revealConversation(element) {
  const target = element?.classList?.contains('thread') ? element : thread.value
  if (opening.value || !target) return
  target.scrollTop = target.scrollHeight
  displayedConversationId = current.value?.id
}

watch(
  [() => current.value?.id, () => messages.value.length, () => isLoading.value, () => error.value],
  async () => {
    if (prependingId && prependingId === current.value?.id) return
    if (opening.value) return
    const id = current.value?.id
    const wasNearBottom =
      !thread.value ||
      thread.value.scrollHeight - thread.value.scrollTop - thread.value.clientHeight < 120
    await nextTick()
    if (opening.value || current.value?.id !== id || !thread.value) return
    if (displayedConversationId !== id) {
      revealConversation()
    } else if (wasNearBottom || messages.value.at(-1)?.local) {
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      thread.value.scrollTo({
        top: thread.value.scrollHeight,
        behavior: reduced ? 'auto' : 'smooth',
      })
    }
  },
)

onMounted(() => {
  refreshHealth()
  initialize()
  window.addEventListener('focus', loadCapabilities)
  window.addEventListener('zhiyan-model-settings-updated', loadCapabilities)
})
onUnmounted(() => {
  window.removeEventListener('focus', loadCapabilities)
  window.removeEventListener('zhiyan-model-settings-updated', loadCapabilities)
  dispose()
})

function modelSettingsUpdated() {
  window.dispatchEvent(new Event('zhiyan-model-settings-updated'))
}
</script>

<template>
  <div class="app-shell">
    <button
      v-if="sidebarOpen"
      type="button"
      class="sidebar-backdrop"
      aria-label="关闭导航"
      @click="sidebarOpen = false"
    ></button>
    <aside class="sidebar" :class="{ 'is-open': sidebarOpen }">
      <div class="brand">
        <span class="brand-mark"><AppIcon name="chat" :size="23" /></span>
        <span>
          知言
          <span class="brand-en">ZHIYAN AI</span>
        </span>
        <button
          class="icon-button mobile-only close-nav"
          type="button"
          aria-label="关闭导航"
          @click="sidebarOpen = false"
        >
          <AppIcon name="close" />
        </button>
      </div>
      <button class="new-chat-button" type="button" @click="startNew">
        <AppIcon name="plus" :size="18" />
        <span>新建对话</span>
        <span class="button-arrow">↗</span>
      </button>
      <button class="coding-entry" type="button" @click="openCoding">
        <span class="coding-entry-code" aria-hidden="true">&lt;/&gt;</span>
        <span>编程工作台</span>
        <span class="button-arrow">↗</span>
      </button>
      <div class="history-heading">
        <span class="sidebar-section-label">历史对话</span>
        <button
          type="button"
          class="icon-button"
          aria-label="刷新历史列表"
          :disabled="listLoading"
          @click="loadList()"
        >
          <AppIcon name="refresh" :size="15" />
        </button>
      </div>
      <nav class="history-list" aria-label="历史对话">
        <p v-if="listError" class="history-list-notice" role="alert">
          {{ listError }}
          <button type="button" @click="loadList()">重新加载</button>
        </p>
        <p v-else-if="!conversations.length" class="history-list-notice">
          {{ listLoading ? '正在加载历史…' : '还没有对话，发送第一条消息后会自动保存。' }}
        </p>
        <div
          v-for="conversation in conversations"
          :key="conversation.id"
          class="history-row"
          :class="{ active: current?.id === conversation.id }"
        >
          <button
            class="history-select"
            type="button"
            :aria-current="current?.id === conversation.id ? 'true' : undefined"
            :title="conversation.title"
            @click="selectConversation(conversation.id)"
          >
            <AppIcon name="chat" :size="16" />
            <span>{{ conversation.title }}</span>
          </button>
          <button
            class="history-action icon-button"
            type="button"
            :aria-label="`重命名对话：${conversation.title}`"
            title="重命名"
            @click="showConversationAction(conversation, 'rename')"
          >
            <AppIcon name="pen" :size="14" />
          </button>
          <button
            class="history-action icon-button"
            type="button"
            :aria-label="`删除对话：${conversation.title}`"
            title="删除"
            :disabled="isLoading && current?.id === conversation.id"
            @click="showConversationAction(conversation, 'delete')"
          >
            <AppIcon name="trash" :size="14" />
          </button>
        </div>
        <button
          v-if="nextCursor"
          class="history-more"
          type="button"
          :disabled="listLoading"
          @click="loadList(true)"
        >
          {{ listLoading ? '正在加载…' : '加载更多对话' }}
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="session-note">
          <AppIcon name="layers" :size="17" />
          <span>
            对话自动保存到你的账号
            <br />
            <small>退出后，重新登录仍可查看</small>
          </span>
        </div>
      </div>
    </aside>

    <main class="main-panel">
      <header class="topbar">
        <div class="topbar-left">
          <button
            class="icon-button mobile-only"
            type="button"
            aria-label="打开导航"
            :aria-expanded="sidebarOpen"
            @click="sidebarOpen = true"
          >
            <AppIcon name="menu" />
          </button>
          <span class="model-label">
            <AppIcon name="spark" :size="15" />
            <span
              class="model-name"
              :title="`${model || 'AI 模型'} · ${attachmentCapabilities?.model_source === 'custom' ? '自定义配置' : '项目默认配置'}`"
            >
              {{ model || 'AI 模型' }}
            </span>
          </span>
        </div>
        <div class="conversation-heading">
          <h1 class="page-label history-page-title" :title="title">
            {{ current ? title : '开始对话' }}
          </h1>
          <span class="conversation-heading-caption">
            {{ opening ? '正在载入对话' : current ? '你的想法，接着聊' : '知言 AI · 你的思考伙伴' }}
          </span>
        </div>
        <div class="topbar-right">
          <button
            class="connection-status"
            type="button"
            :class="health"
            :disabled="health === 'checking'"
            title="检查服务连接（不代表模型调用一定成功）"
            :aria-label="`${statusLabel}，点击检查后端连接`"
            @click="refreshHealth"
          >
            <span class="status-dot"></span>
            <span class="connection-label">{{ statusLabel }}</span>
            <AppIcon v-if="health === 'disconnected'" name="refresh" :size="13" />
          </button>
          <AccountMenu
            :user="user"
            :settings-disabled="isLoading || opening"
            :signing-out="signingOut"
            @settings="openSettings"
            @logout="$emit('logout')"
          />
        </div>
      </header>

      <div
        class="workspace"
        :class="{ 'has-messages': hasMessages || opening || Boolean(current) }"
      >
        <div v-if="notice" class="history-notice" role="status">{{ notice }}</div>
        <div v-if="loadError" class="history-notice history-load-error" role="alert">
          {{ loadError }}
          <button type="button" :disabled="refreshing || submitting" @click="refreshCurrent()">
            重新加载会话
          </button>
        </div>
        <div class="conversation-stage" :aria-busy="opening">
          <Transition name="conversation-view" mode="out-in" @enter="revealConversation">
            <div v-if="opening" key="loading" class="history-loading" role="status">
              <span class="sr-only">正在加载对话…</span>
              <div class="history-skeleton" aria-hidden="true">
                <div class="skeleton-question"></div>
                <div class="skeleton-answer">
                  <span class="mini-brand"><AppIcon name="spark" :size="17" /></span>
                  <div>
                    <i></i>
                    <i></i>
                    <i></i>
                  </div>
                </div>
                <div class="skeleton-question skeleton-question-short"></div>
                <div class="skeleton-answer">
                  <span class="mini-brand"><AppIcon name="spark" :size="17" /></span>
                  <div>
                    <i></i>
                    <i></i>
                  </div>
                </div>
              </div>
            </div>
            <section
              v-else-if="!hasMessages && !loadError"
              key="welcome"
              class="welcome"
              aria-labelledby="welcome-heading"
            >
              <div class="welcome-eyebrow">
                <span class="welcome-symbol"><AppIcon name="spark" :size="28" /></span>
                知言 AI · 你的思考伙伴
              </div>
              <h1 id="welcome-heading">今天想聊些什么？</h1>
              <p class="welcome-description">解答疑惑、整理思路，或把一个想法变成文字。</p>
              <div class="suggestions">
                <button
                  v-for="suggestion in suggestions"
                  :key="suggestion.title"
                  class="suggestion-card"
                  type="button"
                  @click="choosePrompt(suggestion.prompt)"
                >
                  <span class="suggestion-icon">
                    <AppIcon :name="suggestion.icon" :size="20" />
                  </span>
                  <span class="suggestion-title">
                    {{ suggestion.title }}
                    <AppIcon name="chevron" :size="13" />
                  </span>
                  <span class="suggestion-subtitle">{{ suggestion.subtitle }}</span>
                </button>
              </div>
            </section>

            <section
              v-else-if="hasMessages"
              :key="current?.id || 'messages'"
              ref="thread"
              class="thread"
              aria-label="聊天记录"
              aria-live="polite"
              aria-relevant="additions text"
              :aria-busy="isLoading"
            >
              <div class="thread-inner">
                <div class="conversation-date">
                  {{ current?.last_sequence || messages.length }} 条已保存消息
                </div>
                <button
                  v-if="nextBefore"
                  class="older-messages"
                  type="button"
                  :disabled="loadingOlder || submitting || refreshing"
                  @click="showOlderMessages"
                >
                  {{ loadingOlder ? '正在加载…' : '加载更早的消息' }}
                </button>
                <ChatMessage v-for="message in messages" :key="message.id" :message="message" />
                <div v-if="isLoading" class="pending-message" role="status">
                  <span class="mini-brand"><AppIcon name="spark" :size="17" /></span>
                  <span>
                    {{ submitting ? '正在思考' : '回答正在处理中，将自动更新' }}
                    <small class="pending-hint">切换会话将停止本页等待，已保存的消息会保留。</small>
                  </span>
                  <span class="thinking-dots">
                    <i></i>
                    <i></i>
                    <i></i>
                  </span>
                </div>
                <div v-if="error && !isLoading" class="error-notice" role="alert">
                  <div class="error-title">这次回答没有完成</div>
                  <p>{{ error }}</p>
                  <div class="error-actions">
                    <button
                      type="button"
                      :disabled="retrySeconds > 0 || refreshing || Boolean(loadError)"
                      @click="retryAnswer"
                    >
                      <AppIcon name="refresh" :size="14" />
                      {{ retrySeconds > 0 ? `${retrySeconds} 秒后重试` : '重新尝试' }}
                    </button>
                    <button type="button" :disabled="Boolean(loadError)" @click="editQuestion">
                      修改后作为新问题
                    </button>
                  </div>
                </div>
              </div>
            </section>
            <div v-else key="unavailable" class="history-unavailable">
              <AppIcon name="chat" :size="28" />
              <p>这段对话暂时未能加载</p>
              <span>可以在上方重新加载，或先看看其他对话。</span>
            </div>
          </Transition>
        </div>

        <div class="composer-area">
          <form class="composer" :class="{ 'is-waiting': isLoading }" @submit.prevent="submit">
            <div class="source-mode-row" role="group" aria-label="资料使用模式">
              <span>资料模式</span>
              <button
                v-for="option in [
                  { value: 'auto', label: '自动' },
                  { value: 'chat', label: '直接聊天' },
                  { value: 'documents', label: '查资料' },
                ]"
                :key="option.value"
                type="button"
                :class="{ active: sourceMode === option.value }"
                :aria-pressed="sourceMode === option.value"
                :disabled="isLoading || opening || signingOut"
                @click="setSourceMode(option.value)"
              >
                {{ option.label }}
              </button>
            </div>
            <div
              v-if="draftAttachments.length"
              class="attachment-drafts"
              aria-label="待发送附件"
              :aria-busy="attachmentBusy"
            >
              <AttachmentCard
                v-for="item in draftAttachments"
                :key="item.localId || item.id"
                :attachment="item"
                removable
                :disabled="submitting"
                @remove="removeAttachment"
              />
            </div>
            <p v-if="attachmentError" class="attachment-error" role="alert">
              {{ attachmentError }}
            </p>
            <p v-if="draftAttachments.length" class="attachment-hint">
              {{
                sourceMode === 'chat'
                  ? '直接聊天不会读取文字文档；图片仍会交给视觉模型。'
                  : '查资料会检索文字文档片段；图片仍交给视觉模型。请勿上传无权分享的资料。'
              }}
            </p>
            <label class="sr-only" for="message-input">输入你的问题</label>
            <p
              v-if="
                attachmentCapabilities &&
                !attachmentCapabilities.images_enabled &&
                !draftAttachments.length
              "
              class="attachment-hint"
            >
              支持文档附件 · 图片功能需先配置视觉模型
            </p>
            <textarea
              id="message-input"
              ref="textarea"
              v-model="draft"
              rows="2"
              :maxlength="MAX_MESSAGE_LENGTH"
              :disabled="isLoading || opening || Boolean(loadError) || Boolean(error) || signingOut"
              :placeholder="
                error
                  ? '请重试，或将问题修改后重新发送'
                  : isLoading
                    ? '正在等待回答…'
                    : '有什么想问的，尽管说…'
              "
              @keydown="handleKeydown"
            ></textarea>
            <div class="composer-bottom">
              <AttachmentPicker
                :capabilities="attachmentCapabilities"
                :disabled="
                  isLoading ||
                  opening ||
                  refreshing ||
                  Boolean(loadError) ||
                  Boolean(error) ||
                  signingOut ||
                  draftAttachments.length >= 3
                "
                @files="addFiles"
              />
              <span class="composer-context">
                <AppIcon name="chat" :size="15" />
                {{ hasMessages ? '已保存的历史支持继续追问' : '发送后自动保存到历史对话' }}
              </span>
              <span v-if="draft.length > 18000" class="character-count">
                {{ draft.length }} / {{ MAX_MESSAGE_LENGTH }}
              </span>
              <button
                type="submit"
                class="send-button"
                :disabled="!canSend || signingOut"
                aria-label="发送消息"
                title="发送消息"
              >
                <AppIcon name="arrow" :size="21" />
              </button>
            </div>
          </form>
          <div class="composer-footer">
            <span>AI 的回答仅供参考，请核实重要信息</span>
            <span class="keyboard-hint">
              Enter 发送
              <span>·</span>
              Shift + Enter 换行
            </span>
          </div>
        </div>
      </div>
    </main>
    <ConversationDialog
      v-if="actionDialog"
      :conversation="actionDialog.conversation"
      :mode="actionDialog.mode"
      :perform="actionDialog.mode === 'rename' ? renameConversation : deleteConversation"
      @close="actionDialog = null"
    />
    <AccountSettings
      v-if="settingsOpen"
      :user="user"
      :initial-tab="settingsTab"
      @close="settingsOpen = false"
      @updated="$emit('profile-updated', $event)"
      @password-changed="$emit('password-changed', $event)"
      @model-settings-updated="modelSettingsUpdated"
    />
    <RetrievalChoice v-if="routeChoice" @choose="confirmRoute" @cancel="cancelRouteChoice" />
  </div>
</template>
