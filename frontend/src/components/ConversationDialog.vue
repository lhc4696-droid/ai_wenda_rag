<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  conversation: { type: Object, required: true },
  mode: { type: String, required: true },
  perform: { type: Function, required: true },
})
const emit = defineEmits(['close'])
const dialog = ref(null)
const input = ref(null)
const title = ref(props.conversation.title)
const busy = ref(false)
const error = ref('')
let previousFocus
let alive = true
function close() {
  if (!busy.value) emit('close')
}
async function submit() {
  if (busy.value) return
  error.value = ''
  if (
    props.mode === 'rename' &&
    (!title.value.trim() || Array.from(title.value.trim()).length > 80)
  ) {
    error.value = '标题应为 1–80 个字符。'
    return
  }
  busy.value = true
  try {
    await props.perform(props.conversation.id, title.value.trim())
    if (alive) emit('close')
  } catch (cause) {
    if (alive) error.value = cause.message || '操作失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  previousFocus = document.activeElement
  dialog.value?.showModal()
  if (props.mode === 'rename') {
    input.value?.focus()
    input.value?.select()
  }
})
onUnmounted(() => {
  alive = false
  dialog.value?.close()
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="account-dialog conversation-dialog"
      aria-labelledby="conversation-dialog-title"
      @cancel.prevent="close"
    >
      <header class="account-header">
        <h2 id="conversation-dialog-title">{{ mode === 'rename' ? '重命名对话' : '删除对话' }}</h2>
        <button
          type="button"
          class="icon-button"
          aria-label="关闭对话操作"
          :disabled="busy"
          @click="close"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <form @submit.prevent="submit">
        <div v-if="mode === 'rename'" class="auth-field">
          <label for="conversation-title">对话标题</label>
          <input
            id="conversation-title"
            ref="input"
            v-model="title"
            maxlength="80"
            :disabled="busy"
            required
          />
        </div>
        <p v-else class="delete-description">
          确定删除“{{ conversation.title }}”吗？这场对话及全部消息将永久删除，无法恢复。
        </p>
        <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
        <div class="conversation-dialog-actions">
          <button type="button" class="secondary-button" :disabled="busy" @click="close">
            取消
          </button>
          <button
            type="submit"
            :class="mode === 'delete' ? 'danger-button' : 'primary-button'"
            :disabled="busy"
          >
            {{ busy ? '正在处理…' : mode === 'rename' ? '保存标题' : '确认删除' }}
          </button>
        </div>
      </form>
    </dialog>
  </Teleport>
</template>
