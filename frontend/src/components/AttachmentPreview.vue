<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { attachmentUrl } from '../api/attachments.js'
defineProps({ attachment: { type: Object, required: true } })
const emit = defineEmits(['close'])
const dialog = ref(null)
let previous
onMounted(() => {
  previous = document.activeElement
  dialog.value?.showModal()
})
onUnmounted(() => {
  dialog.value?.close()
  if (previous?.isConnected) previous.focus()
})
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="attachment-preview"
      aria-labelledby="attachment-preview-title"
      @cancel.prevent="emit('close')"
    >
      <header>
        <h2 id="attachment-preview-title">{{ attachment.filename }}</h2>
        <button
          type="button"
          class="icon-button"
          aria-label="关闭图片预览"
          autofocus
          @click="emit('close')"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <img :src="attachmentUrl(attachment.id)" :alt="attachment.filename" />
      <p>图片预览 · 已压缩并移除元数据</p>
    </dialog>
  </Teleport>
</template>
