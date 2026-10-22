<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  user: { type: Object, required: true },
  settingsDisabled: { type: Boolean, default: false },
  signingOut: { type: Boolean, default: false },
  menuId: { type: String, default: 'account-dropdown' },
  showModelSettings: { type: Boolean, default: false },
  settingsDisabledHint: { type: String, default: '回答完成后可修改账号' },
})
const emit = defineEmits(['settings', 'logout'])
const root = ref(null)
const trigger = ref(null)
const panel = ref(null)
const open = ref(false)
let pinned = false
let closeTimer

function cancelClose() {
  clearTimeout(closeTimer)
}

function close(restoreFocus = false) {
  cancelClose()
  open.value = false
  pinned = false
  if (restoreFocus) trigger.value?.focus()
}

function hoverOpen(event) {
  if (event.pointerType !== 'mouse' || props.signingOut) return
  cancelClose()
  open.value = true
}

function hoverLeave(event) {
  if (event.pointerType !== 'mouse' || pinned) return
  cancelClose()
  closeTimer = setTimeout(() => {
    // Keyboard focus in the list must not disappear when the pointer leaves.
    if (!panel.value?.contains(document.activeElement)) close()
  }, 180)
}

async function toggle(event) {
  if (props.signingOut) return
  if (open.value && pinned) {
    close()
    return
  }
  cancelClose()
  open.value = true
  pinned = true
  if (event.detail === 0) {
    await nextTick()
    panel.value?.querySelector('button:not(:disabled)')?.focus()
  }
}

async function handleKeys(event) {
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    event.stopPropagation()
    close(true)
    return
  }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  if (!open.value && event.target !== trigger.value) return
  if (props.signingOut) return
  event.preventDefault()
  cancelClose()
  open.value = true
  pinned = true
  await nextTick()
  const items = [...(panel.value?.querySelectorAll('button:not(:disabled)') || [])]
  if (!items.length) return
  const current = items.indexOf(document.activeElement)
  const index =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : current < 0
          ? event.key === 'ArrowUp'
            ? items.length - 1
            : 0
          : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
  items[index].focus()
}

function selectSettings(tab) {
  if (props.settingsDisabled || props.signingOut) return
  close(true)
  emit('settings', tab)
}

function logout() {
  close(true)
  emit('logout')
}

function outsidePointer(event) {
  if (!root.value?.contains(event.target)) close()
}

function focusLeft(event) {
  if (!root.value?.contains(event.relatedTarget)) close()
}

watch(
  () => props.signingOut,
  (value) => {
    if (value) close()
  },
)
onMounted(() => document.addEventListener('pointerdown', outsidePointer))
onUnmounted(() => {
  cancelClose()
  document.removeEventListener('pointerdown', outsidePointer)
})
</script>

<template>
  <div
    ref="root"
    class="account-menu"
    @pointerenter="hoverOpen"
    @pointerleave="hoverLeave"
    @focusout="focusLeft"
    @keydown="handleKeys"
  >
    <button
      ref="trigger"
      class="account-trigger"
      type="button"
      :class="{ 'is-open': open }"
      :aria-label="`账号菜单：${user.username}`"
      :aria-expanded="open"
      :aria-controls="menuId"
      :disabled="signingOut"
      @click="toggle"
    >
      <span class="profile-avatar">{{ Array.from(user.username)[0]?.toUpperCase() }}</span>
      <span class="profile-name" :title="user.username">{{ user.username }}</span>
      <AppIcon class="account-chevron" name="chevron-down" :size="15" />
    </button>
    <div v-if="open" :id="menuId" class="account-dropdown-wrap">
      <div ref="panel" class="account-dropdown" role="group" aria-label="账号菜单">
        <div class="account-dropdown-heading">我的账号</div>
        <button
          type="button"
          class="account-menu-item"
          :disabled="settingsDisabled || signingOut"
          @click="selectSettings('profile')"
        >
          <AppIcon name="user" :size="18" />
          <span>账号设置</span>
        </button>
        <button
          v-if="showModelSettings"
          type="button"
          class="account-menu-item"
          :disabled="settingsDisabled || signingOut"
          @click="selectSettings('models')"
        >
          <AppIcon name="spark" :size="18" />
          <span>模型设置</span>
        </button>
        <p v-if="settingsDisabled" class="account-menu-hint">{{ settingsDisabledHint }}</p>
        <div class="account-menu-divider"></div>
        <button
          type="button"
          class="account-menu-item account-menu-logout"
          :disabled="signingOut"
          @click="logout"
        >
          <AppIcon name="logout" :size="18" />
          <span>{{ signingOut ? '退出中…' : '退出登录' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
