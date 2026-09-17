import { computed, ref } from 'vue'
import { sendChat } from '../api/chat.js'

export const MAX_MESSAGE_LENGTH = 20_000
// 后端最多接收 50 条消息；25 次提问的最后一个请求包含 49 条历史消息。
export const MAX_TURNS = 25

export function useChat(requestChat = sendChat, timeoutMs = 120_000) {
  const messages = ref([])
  const draft = ref('')
  const isLoading = ref(false)
  const error = ref('')
  const model = ref('')
  const retrySeconds = ref(0)
  let cooldownTimer

  function setCooldown(seconds) {
    clearInterval(cooldownTimer)
    const until = Date.now() + seconds * 1000
    const tick = () => {
      retrySeconds.value = Math.max(0, Math.ceil((until - Date.now()) / 1000))
      if (!retrySeconds.value) clearInterval(cooldownTimer)
    }
    tick()
    if (retrySeconds.value) cooldownTimer = setInterval(tick, 1000)
  }
  const turnCount = computed(
    () => messages.value.filter((message) => message.role === 'assistant').length,
  )
  const limitReached = computed(() => turnCount.value >= MAX_TURNS)
  const canSend = computed(
    () =>
      Boolean(draft.value.trim()) &&
      !isLoading.value &&
      !error.value &&
      !limitReached.value &&
      !retrySeconds.value &&
      draft.value.length <= MAX_MESSAGE_LENGTH,
  )
  let controller
  let runId = 0

  async function requestAnswer() {
    const id = ++runId
    const activeController = new AbortController()
    controller = activeController
    error.value = ''
    isLoading.value = true
    // 超时后结束界面等待；取消浏览器请求并不保证上游模型停止生成。
    let timer
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        reject(new Error('这次回答等待时间较长，请稍后重试，或缩短问题。'))
        activeController.abort()
      }, timeoutMs)
    })

    try {
      // 历史 assistant 内容也受输入边界约束；说明文字不混入模型上下文。
      const payload = messages.value.map(({ role, content }) => ({ role, content: Array.from(content).slice(0, MAX_MESSAGE_LENGTH).join('') }))
      const answer = await Promise.race([requestChat(payload, activeController.signal), timeout])
      if (id !== runId) return
      const characters = Array.from(answer.content)
      const notices = []
      if (answer.context_trimmed) notices.push('上下文较长，本次回答仅参考了最近的完整轮次，较早内容未带入。')
      if (answer.answer_truncated || characters.length > MAX_MESSAGE_LENGTH) notices.push('回答达到长度上限，可能尚未完整。你可以缩小问题范围或继续追问。')
      messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content: characters.slice(0, MAX_MESSAGE_LENGTH).join(''), notice: notices.join(' ') })
      model.value = answer.model || ''
    } catch (cause) {
      if (id === runId) {
        error.value = cause instanceof Error ? cause.message : '请求失败，请稍后重试。'
        if (cause.retryAfter) setCooldown(cause.retryAfter)
      }
    } finally {
      clearTimeout(timer)
      if (id === runId) {
        isLoading.value = false
        controller = undefined
      }
    }
  }

  async function send() {
    if (!canSend.value) return
    messages.value.push({ id: crypto.randomUUID(), role: 'user', content: draft.value.trim() })
    draft.value = ''
    await requestAnswer()
  }

  async function retry() {
    if (isLoading.value || retrySeconds.value || !error.value || messages.value.at(-1)?.role !== 'user') return
    await requestAnswer()
  }

  function editFailed() {
    if (isLoading.value || !error.value) return
    const lastMessage = messages.value.at(-1)
    if (lastMessage?.role === 'user') {
      draft.value = messages.value.pop().content
      error.value = ''
    }
  }

  function reset() {
    ++runId
    controller?.abort()
    controller = undefined
    messages.value = []
    draft.value = ''
    error.value = ''
    model.value = ''
    isLoading.value = false
    clearInterval(cooldownTimer)
    retrySeconds.value = 0
  }

  return {
    messages,
    draft,
    isLoading,
    error,
    model,
    retrySeconds,
    turnCount,
    limitReached,
    canSend,
    send,
    retry,
    editFailed,
    reset,
  }
}
