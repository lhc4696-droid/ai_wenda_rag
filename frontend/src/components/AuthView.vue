<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { resetPassword, sendEmailCode, signIn, signUp } from '../api/auth.js'
import { passwordError, usernameError } from '../lib/account-validation.js'

defineProps({ notice: { type: String, default: '' } })
const emit = defineEmits(['authenticated'])
const mode = ref('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const busy = ref(false)
const error = ref('')
const email = ref('')
const code = ref('')
const sendingCode = ref(false)
const feedback = ref('')
const clock = ref(Date.now())
const cooldowns = reactive({})
const registering = computed(() => mode.value === 'register')
const resetting = computed(() => mode.value === 'reset')
const needsEmail = computed(() => registering.value || resetting.value)
const purpose = computed(() => (resetting.value ? 'reset_password' : 'register'))
const cooldownKey = computed(() => `${purpose.value}:${email.value.trim()}`)
const remaining = computed(() =>
  Math.max(0, Math.ceil(((cooldowns[cooldownKey.value] || 0) - clock.value) / 1000)),
)
const locked = computed(() => busy.value || sendingCode.value)
const heading = computed(() =>
  resetting.value ? '找回你的知言账号' : registering.value ? '开启你的知言空间' : '欢迎回到知言',
)
const submitLabel = computed(() =>
  busy.value
    ? '正在处理…'
    : resetting.value
      ? '重置密码'
      : registering.value
        ? '创建账号并进入'
        : '登录并进入',
)
const ticker = setInterval(() => {
  clock.value = Date.now()
}, 500)
onUnmounted(() => clearInterval(ticker))

watch(email, () => {
  code.value = ''
  feedback.value = ''
  error.value = ''
})

function changeMode(nextMode) {
  if (locked.value) return
  mode.value = nextMode
  error.value = ''
  password.value = ''
  confirmPassword.value = ''
  showPassword.value = false
  code.value = ''
  feedback.value = ''
}

function emailLooksValid() {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())
}

async function requestCode() {
  if (locked.value || remaining.value > 0) return
  error.value = ''
  feedback.value = ''
  if (!emailLooksValid()) {
    error.value = '请先填写有效的邮箱地址。'
    return
  }
  sendingCode.value = true
  code.value = ''
  const key = cooldownKey.value
  try {
    const result = await sendEmailCode(email.value.trim(), purpose.value)
    cooldowns[key] = Date.now() + result.retry_after * 1000
    feedback.value = result.message
  } catch (cause) {
    error.value = cause.message
    const wait = cause.retryAfter || (cause.status === 0 ? 60 : 0)
    if (wait) cooldowns[key] = Date.now() + wait * 1000
  } finally {
    clock.value = Date.now()
    sendingCode.value = false
  }
}

async function submit() {
  if (locked.value) return
  error.value = ''
  if (!resetting.value && usernameError(username.value)) {
    error.value = usernameError(username.value)
    return
  }
  if (passwordError(password.value)) {
    error.value = passwordError(password.value)
    return
  }
  if (needsEmail.value && password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致。'
    return
  }
  if (needsEmail.value && !emailLooksValid()) {
    error.value = '请输入有效的邮箱地址。'
    return
  }
  if (needsEmail.value && !/^\d{6}$/.test(code.value)) {
    error.value = '请输入邮件中的 6 位数字验证码。'
    return
  }
  busy.value = true
  try {
    if (resetting.value) {
      const result = await resetPassword(email.value.trim(), code.value, password.value)
      password.value = ''
      confirmPassword.value = ''
      code.value = ''
      showPassword.value = false
      mode.value = 'login'
      feedback.value = result.message
      return
    }
    const result = registering.value
      ? await signUp(username.value.trim(), password.value, email.value.trim(), code.value)
      : await signIn(username.value.trim(), password.value)
    password.value = ''
    confirmPassword.value = ''
    emit('authenticated', result.user)
  } catch (cause) {
    error.value = cause.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-story" aria-label="知言 AI">
      <div class="brand">
        <span class="brand-mark"><AppIcon name="chat" :size="23" /></span>
        <span>
          知言
          <span class="brand-en">ZHIYAN AI</span>
        </span>
      </div>
      <div class="auth-story-content">
        <span class="auth-eyebrow">A SPACE FOR YOUR IDEAS</span>
        <h1>
          与你的下一次
          <br />
          <span>灵感相遇。</span>
        </h1>
        <p>
          从好奇出发，让知识更近一点。
          <br />
          知言陪你提问、表达，也陪你找到新的可能。
        </p>
        <div class="auth-illustration" aria-hidden="true">
          <div class="orbit orbit-one"></div>
          <div class="orbit orbit-two"></div>
          <span class="orbit-spark"><AppIcon name="spark" :size="42" /></span>
          <span class="idea-bubble">一个想法，可以走多远？</span>
          <span class="answer-bubble">
            <AppIcon name="spark" :size="17" />
            我们一起探索。
          </span>
        </div>
      </div>
      <div class="auth-story-footer">
        知言 AI
        <span>让想法继续 ↗</span>
      </div>
    </section>

    <section class="auth-form-panel" aria-labelledby="auth-heading">
      <div class="auth-mobile-brand">
        <AppIcon name="chat" :size="22" />
        知言 AI
      </div>
      <div class="auth-form-container">
        <span class="auth-form-eyebrow">YOUR PERSONAL AI SPACE</span>
        <h2 id="auth-heading">{{ heading }}</h2>
        <p class="auth-description">
          {{
            resetting
              ? '通过已验证的邮箱，安全地设置一个新密码。'
              : registering
                ? '创建一个账号，让好奇心有个落脚的地方。'
                : '登录账号，继续与 AI 的每一次对话。'
          }}
        </p>
        <div class="auth-tabs" aria-label="账号操作">
          <button
            type="button"
            :class="{ active: mode === 'login' }"
            :aria-pressed="mode === 'login'"
            :disabled="locked"
            @click="changeMode('login')"
          >
            登录
          </button>
          <button
            type="button"
            :class="{ active: registering }"
            :aria-pressed="registering"
            :disabled="locked"
            @click="changeMode('register')"
          >
            创建账号
          </button>
        </div>
        <p v-if="(feedback || notice) && !error" class="auth-notice" role="status">
          {{ feedback || notice }}
        </p>
        <form class="auth-form" novalidate @submit.prevent="submit">
          <div v-if="!resetting" class="auth-field">
            <label for="auth-username">用户名</label>
            <input
              id="auth-username"
              v-model="username"
              name="username"
              autocomplete="username"
              autocapitalize="none"
              :spellcheck="false"
              maxlength="32"
              :disabled="locked"
              placeholder="你的用户名"
              aria-describedby="username-hint"
              required
            />
            <small id="username-hint">3–32 位英文字母、数字或下划线</small>
          </div>
          <template v-if="needsEmail">
            <div class="auth-field">
              <label for="auth-email">邮箱</label>
              <input
                id="auth-email"
                v-model="email"
                name="email"
                type="email"
                autocomplete="email"
                maxlength="254"
                :disabled="locked"
                placeholder="你的邮箱地址"
                aria-describedby="email-hint"
                required
              />
              <small id="email-hint">
                {{
                  resetting
                    ? '填写注册时验证过的邮箱；没有绑定邮箱的旧账号暂不支持邮箱找回。'
                    : '用于验证注册和找回密码，请使用你能接收邮件的邮箱。'
                }}
              </small>
            </div>
            <div class="auth-field">
              <label for="auth-code">邮箱验证码</label>
              <div class="email-code-row">
                <input
                  id="auth-code"
                  v-model="code"
                  name="code"
                  type="text"
                  inputmode="numeric"
                  autocomplete="one-time-code"
                  maxlength="6"
                  :disabled="locked"
                  placeholder="6 位数字"
                  aria-describedby="code-hint"
                  required
                />
                <button
                  type="button"
                  class="email-code-button"
                  :disabled="locked || remaining > 0"
                  @click="requestCode"
                >
                  {{
                    sendingCode
                      ? '正在申请…'
                      : remaining > 0
                        ? `${remaining} 秒后重发`
                        : '发送验证码'
                  }}
                </button>
              </div>
              <small id="code-hint">
                请使用本次操作最近申请的验证码，有效期以邮件为准。未收到时请检查垃圾邮件或稍后重试。
              </small>
            </div>
          </template>
          <div class="auth-field">
            <label for="auth-password">{{ resetting ? '新密码' : '密码' }}</label>
            <div class="password-input">
              <input
                id="auth-password"
                v-model="password"
                name="password"
                :type="showPassword ? 'text' : 'password'"
                :autocomplete="needsEmail ? 'new-password' : 'current-password'"
                maxlength="128"
                :disabled="locked"
                placeholder="输入你的密码"
                aria-describedby="password-hint"
                required
              />
              <button
                type="button"
                :disabled="locked"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                {{ showPassword ? '隐藏' : '显示' }}
              </button>
            </div>
            <small id="password-hint">8–128 个字符，建议使用容易记住的长密码</small>
          </div>
          <div v-if="needsEmail" class="auth-field">
            <label for="auth-confirm">确认密码</label>
            <input
              id="auth-confirm"
              v-model="confirmPassword"
              name="confirm-password"
              :type="showPassword ? 'text' : 'password'"
              autocomplete="new-password"
              maxlength="128"
              :disabled="locked"
              placeholder="再次输入密码"
              required
            />
          </div>
          <div v-if="mode === 'login'" class="auth-secondary-actions">
            <button type="button" :disabled="locked" @click="changeMode('reset')">
              忘记密码？
            </button>
          </div>
          <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
          <button class="auth-submit" type="submit" :disabled="locked">
            {{ submitLabel }}
            <AppIcon v-if="!busy" name="chevron" :size="17" />
          </button>
          <p v-if="resetting" class="reset-session-note">
            重置成功后，所有设备的旧登录会失效，需要重新登录。
          </p>
        </form>
        <p class="auth-footnote">
          账号仅用于当前知言应用
          <br />
          对话保存在你的账号下，登录后可继续查看
        </p>
      </div>
      <div class="auth-panel-footer">理解 · 表达 · 探索</div>
    </section>
  </main>
</template>
