import { computed, ref } from 'vue'
import { historyApi } from '../api/conversations.js'

export const MAX_MESSAGE_LENGTH = 20_000

function displayMessage(message) {
  const notes = []
  if (message.context_trimmed) notes.push('本次回答仅参考最近的部分对话，更早消息仍保存在历史中。')
  if (message.answer_truncated)
    notes.push('回答达到长度上限，可能尚未完整，可以缩小问题范围或继续追问。')
  return { ...message, notice: notes.join(' ') }
}

export function useConversations(api = historyApi) {
  const conversations = ref([])
  const current = ref(null)
  const messages = ref([])
  const draft = ref('')
  const error = ref('')
  const notice = ref('')
  const loadError = ref('')
  const listError = ref('')
  const opening = ref(false)
  const refreshing = ref(false)
  const listLoading = ref(false)
  const loadingOlder = ref(false)
  const submitting = ref(false)
  const nextCursor = ref(null)
  const nextBefore = ref(null)
  const retrySeconds = ref(0)
  const model = computed(
    () =>
      [...messages.value].reverse().find((message) => message.role === 'assistant')?.model || '',
  )
  const tail = computed(() => messages.value.at(-1))
  const serverPending = computed(
    () => tail.value?.role === 'user' && tail.value.status === 'pending' && !tail.value.local,
  )
  const isLoading = computed(() => submitting.value || serverPending.value)
  const canSend = computed(
    () =>
      Boolean(draft.value.trim()) &&
      Array.from(draft.value).length <= MAX_MESSAGE_LENGTH &&
      !isLoading.value &&
      !opening.value &&
      !refreshing.value &&
      !loadError.value &&
      !error.value &&
      !retrySeconds.value,
  )
  let view = 0
  let alive = true
  let listRevision = 0
  let createId = crypto.randomUUID()
  let lastSubmission = null
  let sendController, readController, olderController, listController
  let pollTimer, cooldownTimer
  const mutations = new Set()
  const deleted = new Set()

  function updateUrl(id) {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.hash = id ? `conversation=${id}` : ''
    window.history.replaceState(null, '', url)
  }

  function upsert(conversation) {
    if (!alive || deleted.has(conversation.id)) return
    ++listRevision
    conversations.value = [
      conversation,
      ...conversations.value.filter((item) => item.id !== conversation.id),
    ].sort((a, b) => b.updated_at - a.updated_at || b.id.localeCompare(a.id))
    if (current.value?.id === conversation.id) current.value = conversation
  }

  function cooldown(seconds) {
    clearInterval(cooldownTimer)
    const until = Date.now() + seconds * 1000
    const tick = () => {
      retrySeconds.value = Math.max(0, Math.ceil((until - Date.now()) / 1000))
      if (!retrySeconds.value) clearInterval(cooldownTimer)
    }
    tick()
    if (retrySeconds.value) cooldownTimer = setInterval(tick, 1000)
  }

  async function loadList(more = false) {
    if (!alive || listLoading.value || (more && !nextCursor.value)) return
    listLoading.value = true
    listError.value = ''
    listController = new AbortController()
    const revision = listRevision
    try {
      const data = await api.list(more ? nextCursor.value : null, listController.signal)
      if (!alive) return
      const base = more || revision !== listRevision ? conversations.value : []
      const merged = new Map(
        data.items.filter((item) => !deleted.has(item.id)).map((item) => [item.id, item]),
      )
      for (const item of base) merged.set(item.id, item)
      conversations.value = [...merged.values()].sort(
        (a, b) => b.updated_at - a.updated_at || b.id.localeCompare(a.id),
      )
      nextCursor.value = data.next_cursor
    } catch (cause) {
      if (alive) listError.value = cause.message || '历史列表暂时加载失败'
    } finally {
      if (alive) listLoading.value = false
    }
  }

  function leaveView() {
    ++view
    clearTimeout(pollTimer)
    sendController?.abort()
    readController?.abort()
    olderController?.abort()
    submitting.value = opening.value = refreshing.value = loadingOlder.value = false
    lastSubmission = null
    loadError.value = error.value = notice.value = ''
    messages.value = []
    draft.value = ''
    nextBefore.value = null
  }

  function reset() {
    leaveView()
    current.value = null
    createId = crypto.randomUUID()
    updateUrl(null)
  }

  function applySnapshot(data, keepOlder = true) {
    const rows = data.messages.map(displayMessage)
    const older =
      keepOlder && rows.length
        ? messages.value.filter((message) => !message.local && message.sequence < rows[0].sequence)
        : []
    if (lastSubmission) {
      const existing = rows.find(
        (message) =>
          message.client_message_id === lastSubmission.client_message_id.replaceAll('-', ''),
      )
      if (!existing) {
        draft.value = lastSubmission.content
        notice.value = '上次问题尚未保存，已放回输入框。请确认后重新发送。'
      } else if (existing.status === 'completed') {
        notice.value = '已从历史记录恢复本次回答。'
      } else if (existing.status === 'pending') {
        notice.value = '问题已保存，正在确认回答状态…'
      } else {
        notice.value = ''
      }
      lastSubmission = null
    }
    messages.value = [...older, ...rows]
    if (!older.length) nextBefore.value = data.next_before
    current.value = data.conversation
    upsert(data.conversation)
    error.value =
      tail.value?.status === 'failed'
        ? tail.value.error_message || '上次回答未完成，可以重新尝试。'
        : ''
    loadError.value = ''
  }

  function schedulePoll() {
    clearTimeout(pollTimer)
    if (alive && serverPending.value && !submitting.value && !loadError.value) {
      pollTimer = setTimeout(() => refreshCurrent(true), 2000)
    }
  }

  async function refreshCurrent(quiet = false) {
    if (!current.value || !alive || refreshing.value || submitting.value) return
    const id = current.value.id
    const epoch = view
    refreshing.value = true
    readController?.abort()
    const controller = new AbortController()
    readController = controller
    try {
      const data = await api.messages(id, null, controller.signal)
      if (epoch !== view || !alive) return
      applySnapshot(data)
    } catch (cause) {
      if (epoch !== view || !alive) return
      if (cause.status === 404) {
        deleted.add(id)
        conversations.value = conversations.value.filter((item) => item.id !== id)
        reset()
        notice.value = '这场对话已被删除或无法访问，可以开始新的对话。'
      } else {
        loadError.value = quiet
          ? '暂时无法更新回答状态，请刷新会话确认。'
          : cause.message || '加载失败，请重试。'
      }
    } finally {
      if (epoch === view && alive) {
        refreshing.value = false
        schedulePoll()
      }
    }
  }

  async function openConversation(id) {
    if (current.value?.id === id && !loadError.value) return
    leaveView()
    const epoch = view
    current.value = conversations.value.find((item) => item.id === id) || {
      id,
      title: '正在加载…',
      last_sequence: 0,
    }
    updateUrl(id)
    opening.value = true
    await refreshCurrent()
    if (epoch === view) opening.value = false
  }

  async function initialize() {
    const epoch = view
    opening.value = true
    await loadList()
    if (!alive || epoch !== view) return
    opening.value = false
    const hash = typeof window === 'undefined' ? '' : window.location.hash
    const selected = /^#conversation=([a-f0-9]{32})$/.exec(hash)?.[1]
    const id = selected || conversations.value[0]?.id
    if (id) await openConversation(id)
  }

  async function loadOlder() {
    if (
      !nextBefore.value ||
      !current.value ||
      loadingOlder.value ||
      opening.value ||
      submitting.value ||
      refreshing.value
    )
      return false
    const epoch = view
    loadingOlder.value = true
    olderController = new AbortController()
    try {
      const data = await api.messages(current.value.id, nextBefore.value, olderController.signal)
      if (epoch !== view || !alive) return false
      const present = new Set(messages.value.map((message) => message.id))
      messages.value = [
        ...data.messages.filter((message) => !present.has(message.id)).map(displayMessage),
        ...messages.value,
      ]
      nextBefore.value = data.next_before
      return true
    } catch (cause) {
      if (epoch === view && alive) notice.value = cause.message || '更早消息加载失败，请重试。'
      return false
    } finally {
      if (epoch === view) loadingOlder.value = false
    }
  }

  async function submit(data, isRetry = false) {
    const epoch = view
    const controller = new AbortController()
    sendController = controller
    submitting.value = true
    clearTimeout(pollTimer)
    error.value = notice.value = ''
    try {
      if (!current.value) {
        const conversation = await api.create(createId, controller.signal)
        if (epoch !== view || !alive) return
        current.value = conversation
        updateUrl(conversation.id)
        upsert(conversation)
      }
      lastSubmission = data
      if (!isRetry) {
        messages.value.push({
          id: data.client_message_id,
          client_message_id: data.client_message_id.replaceAll('-', ''),
          sequence: current.value.last_sequence + 1,
          role: 'user',
          content: data.content,
          status: 'pending',
          local: true,
        })
        draft.value = ''
      } else if (tail.value) tail.value.status = 'pending'
      const result = await api.send(
        current.value.id,
        { ...data, retry: isRetry },
        controller.signal,
      )
      if (epoch !== view || !alive) return
      // 以服务端消息为准，替换乐观显示；重放也不会追加重复消息。
      const ids = new Set([result.user_message.id, result.assistant_message.id])
      messages.value = messages.value.filter(
        (message) =>
          message.client_message_id !== result.user_message.client_message_id &&
          !ids.has(message.id),
      )
      messages.value.push(
        displayMessage(result.user_message),
        displayMessage(result.assistant_message),
      )
      messages.value.sort((a, b) => a.sequence - b.sequence)
      upsert(result.conversation)
      lastSubmission = null
      if (result.conversation.last_sequence > result.assistant_message.sequence) {
        submitting.value = false
        await refreshCurrent()
      }
    } catch (cause) {
      if (epoch !== view || !alive) return
      if (cause.retryAfter) cooldown(cause.retryAfter)
      notice.value = cause.message || '本次请求未完成，请刷新确认。'
      if (current.value) {
        // 不能因为响应丢失就再次调用模型；先读取已保存结果。
        submitting.value = false
        await refreshCurrent()
      } else {
        draft.value = data.content
      }
    } finally {
      if (epoch === view && alive) {
        submitting.value = false
        sendController = null
        schedulePoll()
      }
    }
  }

  async function send() {
    if (!canSend.value) return
    await submit({
      content: draft.value.trim(),
      client_message_id: crypto.randomUUID(),
      expected_last_sequence: current.value?.last_sequence || 0,
    })
  }

  async function retry() {
    if (
      isLoading.value ||
      opening.value ||
      refreshing.value ||
      loadError.value ||
      retrySeconds.value ||
      tail.value?.status !== 'failed'
    )
      return
    await submit(
      {
        content: tail.value.content,
        client_message_id: tail.value.client_message_id,
        expected_last_sequence: tail.value.sequence - 1,
      },
      true,
    )
  }

  function editFailed() {
    if (isLoading.value || tail.value?.status !== 'failed' || loadError.value) return
    draft.value = tail.value.content
    error.value = ''
    notice.value = '原问题保留在历史中，修改后的问题会作为新消息发送。'
  }

  async function renameConversation(id, title) {
    const controller = new AbortController()
    mutations.add(controller)
    try {
      const item = await api.rename(id, title, controller.signal)
      upsert(item)
    } finally {
      mutations.delete(controller)
    }
  }

  async function deleteConversation(id) {
    const controller = new AbortController()
    mutations.add(controller)
    try {
      await api.remove(id, controller.signal)
      if (!alive) return
      deleted.add(id)
      ++listRevision
      conversations.value = conversations.value.filter((item) => item.id !== id)
      if (current.value?.id === id) reset()
    } finally {
      mutations.delete(controller)
    }
  }

  function dispose() {
    alive = false
    leaveView()
    listController?.abort()
    for (const controller of mutations) controller.abort()
    clearInterval(cooldownTimer)
    updateUrl(null)
  }

  return {
    conversations,
    current,
    messages,
    draft,
    error,
    notice,
    loadError,
    listError,
    opening,
    refreshing,
    listLoading,
    loadingOlder,
    nextCursor,
    nextBefore,
    model,
    isLoading,
    submitting,
    canSend,
    retrySeconds,
    initialize,
    loadList,
    openConversation,
    refreshCurrent,
    loadOlder,
    reset,
    send,
    retry,
    editFailed,
    renameConversation,
    deleteConversation,
    dispose,
  }
}
