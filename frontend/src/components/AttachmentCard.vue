<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import AttachmentPreview from './AttachmentPreview.vue'
import { attachmentUrl } from '../api/attachments.js'
const props = defineProps({
  attachment: { type: Object, required: true },
  removable: Boolean,
  disabled: Boolean,
})
defineEmits(['remove'])
const preview = ref(false)
const unavailable = ref(false)
const size = computed(() =>
  props.attachment.size >= 1048576
    ? `${(props.attachment.size / 1048576).toFixed(1)} MB`
    : `${Math.ceil(props.attachment.size / 1024)} KB`,
)
const ready = computed(() => props.attachment.status === 'ready' && Boolean(props.attachment.id))
</script>

<template>
  <div class="attachment-card" :class="{ 'is-failed': attachment.status === 'failed' }">
    <button
      v-if="attachment.kind === 'image' && ready && !unavailable"
      class="attachment-thumbnail"
      type="button"
      :aria-label="`预览图片：${attachment.filename}`"
      @click="preview = true"
    >
      <img
        :src="attachmentUrl(attachment.id)"
        :alt="attachment.filename"
        loading="lazy"
        @error="unavailable = true"
      />
    </button>
    <span v-else class="attachment-file-icon"><AppIcon name="book" :size="21" /></span>
    <div class="attachment-details">
      <a
        v-if="ready && attachment.kind === 'document'"
        :href="attachmentUrl(attachment.id)"
        :title="attachment.filename"
        download
      >
        {{ attachment.filename }}
      </a>
      <strong v-else :title="attachment.filename">{{ attachment.filename }}</strong>
      <span>
        {{ size }} ·
        {{
          attachment.status === 'uploading'
            ? '正在上传并解析…'
            : attachment.status === 'failed'
              ? '处理失败'
              : unavailable
                ? '预览不可用'
                : attachment.kind === 'image'
                  ? '图片已就绪'
                  : '正文已提取'
        }}
      </span>
      <small v-if="attachment.error_message" role="alert">{{ attachment.error_message }}</small>
      <small v-else-if="attachment.warning">{{ attachment.warning }}</small>
    </div>
    <button
      v-if="removable"
      class="icon-button attachment-remove"
      type="button"
      :disabled="disabled"
      :aria-label="`移除附件：${attachment.filename}`"
      @click="$emit('remove', attachment)"
    >
      <AppIcon name="close" :size="15" />
    </button>
    <AttachmentPreview v-if="preview" :attachment="attachment" @close="preview = false" />
  </div>
</template>
