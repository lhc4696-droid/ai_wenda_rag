<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
const props = defineProps({ capabilities: Object, disabled: Boolean })
const emit = defineEmits(['files'])
const input = ref(null)
const accept = computed(() =>
  (props.capabilities?.extensions || [])
    .filter(
      (ext) =>
        props.capabilities.images_enabled || !['.jpg', '.jpeg', '.png', '.webp'].includes(ext),
    )
    .join(','),
)
function selected(event) {
  emit('files', [...event.target.files])
  event.target.value = ''
}
</script>

<template>
  <div class="attachment-picker">
    <input
      ref="input"
      type="file"
      hidden
      multiple
      :accept="accept"
      :disabled="disabled || !capabilities"
      aria-label="选择聊天附件"
      @change="selected"
    />
    <button
      class="icon-button attachment-pick-button"
      type="button"
      :disabled="disabled || !capabilities"
      aria-label="添加附件"
      :title="
        capabilities
          ? `最多 3 个附件，每个不超过 ${Math.floor(capabilities.max_bytes / 1048576)} MB`
          : '正在读取附件能力'
      "
      @click="input?.click()"
    >
      <AppIcon name="plus" :size="20" />
    </button>
  </div>
</template>
