<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AuthView from './components/AuthView.vue'
import ChatWorkspace from './components/ChatWorkspace.vue'
import AppIcon from './components/AppIcon.vue'
import { currentUser, signOut } from './api/auth.js'

const user = ref(null)
const checking = ref(true)
const notice = ref('')
const connectionError = ref('')
const signingOut = ref(false)
let generation = 0
let channel

async function loadSession({ invalidate = false } = {}) {
  const id = ++generation
  if (invalidate) user.value = null
  if (!user.value) checking.value = true
  connectionError.value = ''
  try {
    const result = await currentUser()
    if (id === generation) user.value = result.user
  } catch (error) {
    if (id !== generation) return
    if (error.status === 401) {
      if (user.value) notice.value = '登录已过期，请重新登录。'
      user.value = null
    } else {
      connectionError.value = error.message
    }
  } finally {
    if (id === generation) checking.value = false
  }
}

function authenticated(account) {
  ++generation
  user.value = account
  checking.value = false
  notice.value = ''
  connectionError.value = ''
  channel?.postMessage('changed')
}

function expired() {
  ++generation
  user.value = null
  checking.value = false
  connectionError.value = ''
  notice.value = '登录已过期，请重新登录后继续。'
}

function profileUpdated(account) {
  ++generation
  user.value = account
  connectionError.value = ''
  // ID 不变时不卸载 ChatWorkspace，保留当前页面内聊天。
  channel?.postMessage('profile-updated')
}

function passwordChanged() {
  ++generation
  checking.value = false
  notice.value = ''
  connectionError.value = ''
  // 新 Cookie 已由浏览器接收；同一浏览器的标签页共享它，无需卸载聊天。
  channel?.postMessage('session-rotated')
}

async function logout() {
  if (signingOut.value) return
  // 使正在执行的 /me 校验失效，防止旧响应在退出后恢复用户。
  ++generation
  signingOut.value = true
  connectionError.value = ''
  try {
    await signOut()
    ++generation
    user.value = null
    notice.value = '已退出登录。'
    channel?.postMessage('changed')
  } catch (error) {
    connectionError.value = '退出未完成：' + error.message
  } finally {
    signingOut.value = false
  }
}

function focusChanged() {
  if (document.visibilityState === 'visible' && user.value && !signingOut.value) loadSession()
}

onMounted(() => {
  loadSession()
  window.addEventListener('auth-expired', expired)
  document.addEventListener('visibilitychange', focusChanged)
  if ('BroadcastChannel' in window) {
    channel = new BroadcastChannel('zhiyan-auth')
    channel.onmessage = (event) =>
      loadSession({
        invalidate: !['profile-updated', 'session-rotated'].includes(event.data),
      })
  }
})
onUnmounted(() => {
  ++generation
  channel?.close()
  window.removeEventListener('auth-expired', expired)
  document.removeEventListener('visibilitychange', focusChanged)
})
</script>

<template>
  <div v-if="checking" class="auth-loading" role="status">
    <span class="brand-mark"><AppIcon name="chat" :size="23" /></span>
    <span>正在打开你的知言空间…</span>
  </div>
  <template v-else-if="user">
    <div v-if="connectionError" class="session-banner" role="alert">{{ connectionError }}</div>
    <ChatWorkspace
      :key="user.id"
      :user="user"
      :signing-out="signingOut"
      @logout="logout"
      @profile-updated="profileUpdated"
      @password-changed="passwordChanged"
    />
  </template>
  <div v-else-if="connectionError" class="auth-loading">
    <span class="brand-mark"><AppIcon name="chat" :size="23" /></span>
    <p role="alert">{{ connectionError }}</p>
    <button class="auth-submit" type="button" @click="loadSession()">重新连接</button>
  </div>
  <AuthView v-else :notice="notice" @authenticated="authenticated" />
</template>
