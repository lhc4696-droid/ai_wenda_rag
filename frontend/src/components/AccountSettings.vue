<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import ModelSettings from './ModelSettings.vue'
import { accountUsage, changePassword, renameAccount } from '../api/auth.js'
import { passwordError, usernameError } from '../lib/account-validation.js'

const props = defineProps({
  user: { type: Object, required: true },
  initialTab: {
    type: String,
    default: 'profile',
    validator: (value) => ['profile', 'security', 'models'].includes(value),
  },
})
const emit = defineEmits(['close', 'updated', 'password-changed', 'model-settings-updated'])
const dialog = ref(null)
const activeTab = ref(props.initialTab)
const username = ref(props.user.username)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const busy = ref('')
const modelBusy = ref(false)
const settingsBusy = computed(() => Boolean(busy.value) || modelBusy.value)
const tabs = ['profile', 'security', 'models']
const error = ref('')
const feedback = ref('')
const retrySeconds = ref(0)
const usage = ref(null)
const usageError = ref('')
let cooldownTimer
let alive = true
let previousFocus

function close() {
  if (!settingsBusy.value) emit('close')
}

function changeTab(tab) {
  if (settingsBusy.value || activeTab.value === tab) return
  activeTab.value = tab
  error.value = feedback.value = ''
  currentPassword.value = newPassword.value = confirmPassword.value = ''
}

function tabKeys(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  if (settingsBusy.value) return
  const direction = event.key === 'ArrowLeft' ? -1 : 1
  const index = (tabs.indexOf(activeTab.value) + direction + tabs.length) % tabs.length
  const tab = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs[index]
  changeTab(tab)
  dialog.value?.querySelector(`#account-tab-${tab}`)?.focus()
}

function handleFailure(cause) {
  error.value = cause.message
  if (cause.status === 0) error.value += ' 操作结果尚未确认，请刷新资料或重新登录确认后再试。'
  if (cause.retryAfter) {
    const until = Date.now() + cause.retryAfter * 1000
    clearInterval(cooldownTimer)
    const tick = () => {
      retrySeconds.value = Math.max(0, Math.ceil((until - Date.now()) / 1000))
      if (!retrySeconds.value) clearInterval(cooldownTimer)
    }
    tick()
    cooldownTimer = setInterval(tick, 1000)
  }
}

async function saveUsername() {
  if (settingsBusy.value || retrySeconds.value) return
  feedback.value = ''
  error.value = usernameError(username.value)
  if (error.value) return
  if (username.value.trim() === props.user.username) {
    error.value = '用户名没有变化。'
    return
  }
  busy.value = 'username'
  try {
    const result = await renameAccount(username.value.trim())
    if (!alive) return
    username.value = result.user.username
    emit('updated', result.user)
    feedback.value = '用户名已更新。当前登录和对话保留，后续请使用新用户名登录。'
  } catch (cause) {
    if (alive) handleFailure(cause)
  } finally {
    busy.value = ''
  }
}

async function savePassword() {
  if (settingsBusy.value || retrySeconds.value) return
  feedback.value = ''
  error.value = passwordError(currentPassword.value) || passwordError(newPassword.value)
  if (error.value) return
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致。'
    return
  }
  if (currentPassword.value === newPassword.value) {
    error.value = '新密码不能与当前密码相同。'
    return
  }
  busy.value = 'password'
  try {
    const result = await changePassword(currentPassword.value, newPassword.value)
    if (!alive) return
    currentPassword.value = newPassword.value = confirmPassword.value = ''
    feedback.value = result.message
    emit('password-changed', result.message)
  } catch (cause) {
    if (alive) handleFailure(cause)
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  previousFocus = document.activeElement
  dialog.value?.showModal()
  dialog.value?.querySelector(`#account-tab-${activeTab.value}`)?.focus()
  try {
    const result = await accountUsage()
    if (alive) usage.value = result
  } catch (cause) {
    if (alive) usageError.value = '暂时无法读取调用统计：' + cause.message
  }
})
onUnmounted(() => {
  alive = false
  clearInterval(cooldownTimer)
  currentPassword.value = newPassword.value = confirmPassword.value = ''
  dialog.value?.close()
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="account-dialog"
      aria-labelledby="account-title"
      @cancel.prevent="close"
    >
      <header class="account-header">
        <div>
          <span class="account-eyebrow">YOUR ACCOUNT</span>
          <h2 id="account-title">账号设置</h2>
        </div>
        <button
          type="button"
          class="icon-button"
          aria-label="关闭账号设置"
          :disabled="settingsBusy"
          @click="close"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <div class="account-tabs" role="tablist" aria-label="账号设置分类" @keydown="tabKeys">
        <button
          id="account-tab-profile"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'profile'"
          aria-controls="account-panel-profile"
          :tabindex="activeTab === 'profile' ? 0 : -1"
          :disabled="settingsBusy"
          @click="changeTab('profile')"
        >
          <AppIcon name="user" :size="17" />
          账号资料
        </button>
        <button
          id="account-tab-security"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'security'"
          aria-controls="account-panel-security"
          :tabindex="activeTab === 'security' ? 0 : -1"
          :disabled="settingsBusy"
          @click="changeTab('security')"
        >
          <AppIcon name="lock" :size="17" />
          安全设置
        </button>
        <button
          id="account-tab-models"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'models'"
          aria-controls="account-panel-models"
          :tabindex="activeTab === 'models' ? 0 : -1"
          :disabled="settingsBusy"
          @click="changeTab('models')"
        >
          <AppIcon name="spark" :size="17" />
          模型设置
        </button>
      </div>
      <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
      <p v-else-if="feedback" class="auth-notice" role="status">{{ feedback }}</p>
      <p v-if="retrySeconds" class="account-hint" role="status">
        请在 {{ retrySeconds }} 秒后再试。
      </p>
      <section
        v-show="activeTab === 'profile'"
        id="account-panel-profile"
        role="tabpanel"
        aria-labelledby="account-tab-profile"
      >
        <div class="account-summary">
          <span class="profile-avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span>
          <div>
            <strong>{{ user.username }}</strong>
            <p>{{ user.email || '此旧账号尚未绑定邮箱，暂不支持邮箱找回' }}</p>
            <small v-if="user.email">已验证邮箱 · 本版暂不支持更换</small>
          </div>
        </div>
        <div v-if="usage" class="account-usage">
          <span>今日模型调用</span>
          <strong>
            {{ usage.requests }}
            <small>/ {{ usage.daily_limit }} 次</small>
          </strong>
          <p>
            成功 {{ usage.succeeded }} 次 · 额度于
            {{ new Date(usage.resets_at * 1000).toLocaleString() }} 重置
          </p>
          <p>已受理的失败调用也计入额度，此处不是聊天历史。</p>
        </div>
        <p v-else class="account-usage">{{ usageError || '正在读取调用统计…' }}</p>
        <form class="account-form" novalidate @submit.prevent="saveUsername">
          <h3>修改用户名</h3>
          <p class="account-hint">这是你的登录用户名，不是昵称；修改后需使用新用户名登录。</p>
          <div class="auth-field">
            <label for="account-username">新用户名</label>
            <input
              id="account-username"
              v-model="username"
              autocomplete="username"
              autocapitalize="none"
              :spellcheck="false"
              maxlength="32"
              :disabled="Boolean(busy)"
              aria-describedby="account-name-hint"
              required
            />
            <small id="account-name-hint">
              3–32 位英文字母、数字或下划线，不能与其他账号重复。
            </small>
          </div>
          <button class="auth-submit" type="submit" :disabled="Boolean(busy) || retrySeconds > 0">
            {{ busy === 'username' ? '正在保存…' : '保存用户名' }}
          </button>
        </form>
      </section>
      <section
        v-show="activeTab === 'security'"
        id="account-panel-security"
        role="tabpanel"
        aria-labelledby="account-tab-security"
      >
        <form class="account-form account-security-form" novalidate @submit.prevent="savePassword">
          <h3>修改密码</h3>
          <p class="account-hint">新密码为 8–128 个字符，不能全部为空格。</p>
          <div class="auth-field">
            <label for="account-current-password">当前密码</label>
            <input
              id="account-current-password"
              v-model="currentPassword"
              type="password"
              autocomplete="current-password"
              maxlength="128"
              :disabled="Boolean(busy)"
              required
            />
          </div>
          <div class="auth-field">
            <label for="account-new-password">新密码</label>
            <input
              id="account-new-password"
              v-model="newPassword"
              type="password"
              autocomplete="new-password"
              maxlength="128"
              :disabled="Boolean(busy)"
              required
            />
          </div>
          <div class="auth-field">
            <label for="account-confirm-password">确认新密码</label>
            <input
              id="account-confirm-password"
              v-model="confirmPassword"
              type="password"
              autocomplete="new-password"
              maxlength="128"
              :disabled="Boolean(busy)"
              required
            />
          </div>
          <p class="account-warning">
            修改成功后，当前浏览器保持登录，其他设备需要重新登录。当前对话和已保存的聊天记录都会保留。
          </p>
          <button class="auth-submit" type="submit" :disabled="Boolean(busy) || retrySeconds > 0">
            {{ busy === 'password' ? '正在修改…' : '保存新密码' }}
          </button>
        </form>
      </section>
      <section
        v-if="activeTab === 'models'"
        id="account-panel-models"
        role="tabpanel"
        aria-labelledby="account-tab-models"
      >
        <ModelSettings
          @busy="modelBusy = $event"
          @updated="emit('model-settings-updated', $event)"
        />
      </section>
    </dialog>
  </Teleport>
</template>
