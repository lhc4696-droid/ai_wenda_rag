import test from 'node:test'
import assert from 'node:assert/strict'
import { useConversations } from '../src/composables/useConversations.js'

const conversation = {
  id: 'a'.repeat(32),
  title: '测试会话',
  created_at: 1,
  updated_at: 1,
  last_sequence: 0,
}
const clone = (value) => structuredClone(value)
function turn(data) {
  const user = {
    id: 'question',
    client_message_id: data.client_message_id.replaceAll('-', ''),
    sequence: 1,
    role: 'user',
    content: data.content,
    status: 'completed',
  }
  const answer = {
    id: 'reply',
    client_message_id: user.client_message_id,
    sequence: 2,
    role: 'assistant',
    content: '已保存的回答',
    status: 'completed',
    model: 'test',
  }
  return {
    conversation: { ...conversation, last_sequence: 2 },
    user_message: user,
    assistant_message: answer,
  }
}
const pageFor = (result) => ({
  conversation: result.conversation,
  messages: [result.user_message, result.assistant_message],
  next_before: null,
})

test('发送仅传新问题和会话版本，不提交伪造历史；不再有25轮页面限制', async (t) => {
  const calls = []
  const chat = useConversations({
    create: async () => clone(conversation),
    send: async (id, data) => {
      calls.push({ id, data })
      return turn(data)
    },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '我叫小明'
  await chat.send()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].id, conversation.id)
  assert.equal(calls[0].data.expected_last_sequence, 0)
  assert.equal(calls[0].data.content, '我叫小明')
  assert.equal(Object.hasOwn(calls[0].data, 'messages'), false)
  assert.equal(chat.messages.value.length, 2)
  chat.messages.value = Array.from({ length: 60 }, (_, i) => ({
    id: String(i),
    sequence: i + 1,
    role: i % 2 ? 'assistant' : 'user',
    status: 'completed',
    content: '内容',
  }))
  chat.draft.value = '继续'
  assert.equal(chat.canSend.value, true)
})

test('响应丢失先从数据库恢复答案，不再次调用模型', async (t) => {
  let result,
    calls = 0
  const chat = useConversations({
    create: async () => clone(conversation),
    send: async (_id, data) => {
      calls++
      result = turn(data)
      throw new Error('响应丢失')
    },
    messages: async () => pageFor(result),
  })
  t.after(() => chat.dispose())
  chat.draft.value = '已被保存的问题'
  await chat.send()
  assert.equal(chat.messages.value.length, 2)
  assert.equal(chat.messages.value.at(-1).content, '已保存的回答')
  assert.equal(chat.error.value, '')
  await chat.retry()
  assert.equal(calls, 1)
})

test('明确失败后重试复用问题编号，修改问题保留旧记录', async (t) => {
  const calls = []
  let saved
  const chat = useConversations({
    create: async () => clone(conversation),
    send: async (_id, data) => {
      calls.push(clone(data))
      saved = turn(data)
      if (calls.length === 1) throw new Error('模型失败')
      return saved
    },
    messages: async () => ({
      conversation: { ...conversation, last_sequence: 1 },
      messages: [{ ...saved.user_message, status: 'failed', error_message: '模型失败' }],
      next_before: null,
    }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = '问题'
  await chat.send()
  assert.equal(chat.messages.value.length, 1)
  chat.editFailed()
  assert.equal(chat.draft.value, '问题')
  assert.equal(chat.messages.value.length, 1)
  await chat.retry()
  assert.equal(calls[0].client_message_id.replaceAll('-', ''), calls[1].client_message_id)
  assert.equal(calls[1].retry, true)
  assert.equal(chat.messages.value.length, 2)
})

test('切换会话取消旧等待，旧响应不能串入另一会话', async (t) => {
  let resolveSend, started
  const ready = new Promise((resolve) => {
    started = resolve
  })
  let signal, result
  const other = { ...conversation, id: 'b'.repeat(32), title: '另一场对话' }
  const chat = useConversations({
    create: async () => clone(conversation),
    send: (_id, data, currentSignal) => {
      signal = currentSignal
      result = turn(data)
      started()
      return new Promise((resolve) => {
        resolveSend = resolve
      })
    },
    messages: async () => ({ conversation: other, messages: [], next_before: null }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = '旧问题'
  const pending = chat.send()
  await ready
  await chat.openConversation(other.id)
  assert.equal(signal.aborted, true)
  resolveSend(result)
  await pending
  assert.equal(chat.current.value.id, other.id)
  assert.deepEqual(chat.messages.value, [])
  assert.equal(chat.isLoading.value, false)
})

test('消息未受理时恢复输入，状态查不清时阻止直接再次发送', async (t) => {
  let lookupFails = true
  const chat = useConversations({
    create: async () => clone(conversation),
    send: async () => {
      throw new Error('网络中断')
    },
    messages: async () => {
      if (lookupFails) throw new Error('仍未连接')
      return { conversation: clone(conversation), messages: [], next_before: null }
    },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '不要丢失的问题'
  await chat.send()
  assert.equal(chat.canSend.value, false)
  assert.equal(chat.loadError.value, '仍未连接')
  lookupFails = false
  await chat.refreshCurrent()
  assert.equal(chat.draft.value, '不要丢失的问题')
  assert.equal(chat.canSend.value, true)
})

test('删除失败保留列表，删除成功后才清空当前会话', async (t) => {
  let fail = true
  const chat = useConversations({
    list: async () => ({ items: [clone(conversation)], next_cursor: null }),
    messages: async () => ({ conversation: clone(conversation), messages: [], next_before: null }),
    remove: async () => {
      if (fail) throw new Error('删除失败')
    },
  })
  t.after(() => chat.dispose())
  await chat.initialize()
  await assert.rejects(chat.deleteConversation(conversation.id), /删除失败/)
  assert.equal(chat.conversations.value.length, 1)
  fail = false
  await chat.deleteConversation(conversation.id)
  assert.equal(chat.conversations.value.length, 0)
  assert.equal(chat.current.value, null)
})
