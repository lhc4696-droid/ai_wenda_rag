<script setup>
import { computed, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import AttachmentCard from './AttachmentCard.vue'
import { renderMarkdown } from '../lib/markdown.js'

const props = defineProps({ message: { type: Object, required: true } })
const html = computed(() =>
  props.message.role === 'assistant' ? renderMarkdown(props.message.content) : '',
)
const copyState = ref('')
let timer

async function copy() {
  clearTimeout(timer)
  try {
    await navigator.clipboard.writeText(props.message.content)
    copyState.value = '已复制'
  } catch {
    copyState.value = '复制失败，请选择文字复制'
  }
  timer = setTimeout(() => {
    copyState.value = ''
  }, 2500)
}
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <article
    class="message"
    :class="`message--${message.role}`"
    :aria-label="message.role === 'user' ? '你的问题' : 'AI 的回答'"
    :aria-busy="message.streaming || undefined"
  >
    <template v-if="message.role === 'assistant'">
      <div class="assistant-label">
        <span class="mini-brand"><AppIcon name="spark" :size="17" /></span>
        知言
        <span class="ai-tag">AI</span>
      </div>
      <div class="markdown-body" v-html="html"></div>
      <div v-if="message.sources?.length" class="answer-sources" aria-label="回答参考资料">
        <strong>参考资料</strong>
        <details
          v-for="(source, index) in message.sources"
          :key="`${source.attachment_id}-${index}`"
        >
          <summary>
            [{{ index + 1 }}] {{ source.filename
            }}{{ source.page ? ` · 第 ${source.page} 页` : '' }}
          </summary>
          <p>{{ source.snippet }}</p>
          <a
            :href="`/api/attachments/${source.attachment_id}/content`"
            target="_blank"
            rel="noopener noreferrer"
          >
            查看原文件
          </a>
        </details>
      </div>
      <p v-if="message.notice" class="answer-notice">{{ message.notice }}</p>
      <button
        v-if="!message.streaming"
        class="copy-button"
        type="button"
        aria-label="复制回答"
        @click="copy"
      >
        <AppIcon :name="copyState === '已复制' ? 'check' : 'copy'" :size="14" />
        <span aria-live="polite">{{ copyState || '复制' }}</span>
      </button>
    </template>
    <template v-else>
      <span class="user-label">你</span>
      <div class="user-bubble">{{ message.content }}</div>
      <span v-if="message.source_mode === 'documents'" class="message-source-mode">查资料</span>
      <div v-if="message.attachments?.length" class="message-attachments" aria-label="消息附件">
        <AttachmentCard
          v-for="attachment in message.attachments"
          :key="attachment.id"
          :attachment="attachment"
        />
      </div>
      <span v-if="message.status === 'failed'" class="message-status">这条问题未获得回答</span>
    </template>
  </article>
</template>
