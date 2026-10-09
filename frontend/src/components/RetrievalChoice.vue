<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

const emit = defineEmits(['choose', 'cancel'])
const dialog = ref(null)
let previousFocus

onMounted(() => {
  previousFocus = document.activeElement
  dialog.value?.showModal()
})
onUnmounted(() => {
  dialog.value?.close()
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="account-dialog retrieval-dialog"
      aria-labelledby="retrieval-dialog-title"
      aria-describedby="retrieval-dialog-description"
      @cancel.prevent="emit('cancel')"
    >
      <header class="account-header">
        <h2 id="retrieval-dialog-title">这次需要查资料吗？</h2>
        <button type="button" class="icon-button" aria-label="关闭选择" @click="emit('cancel')">
          <AppIcon name="close" />
        </button>
      </header>
      <p id="retrieval-dialog-description" class="retrieval-description">
        这个问题可能是在追问资料，也可能是普通聊天。选择前不会发送问题或调用模型。
      </p>
      <div class="retrieval-choice-actions">
        <button type="button" class="retrieval-choice-button" @click="emit('choose', 'documents')">
          <strong>查资料</strong>
          <small>在当前对话上传的文字文档中检索；没有文档时会提示上传。</small>
        </button>
        <button type="button" class="retrieval-choice-button" @click="emit('choose', 'chat')">
          <strong>直接聊天</strong>
          <small>不查询 Qdrant，也不把文档正文放入本次模型上下文。</small>
        </button>
      </div>
      <button type="button" class="retrieval-cancel" @click="emit('cancel')">先不发送</button>
    </dialog>
  </Teleport>
</template>
