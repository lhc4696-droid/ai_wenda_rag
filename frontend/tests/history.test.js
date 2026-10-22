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

test('流式增量在保存之前可见，完成结果替换临时回答且不重复问题', async (t) => {
  let finish, emit, submitted
  const started = new Promise((resolve) => {
    submitted = resolve
  })
  const chat = useConversations({
    create: async () => clone(conversation),
    sendStream: (_id, data, _signal, onDelta) => {
      emit = onDelta
      submitted()
      return new Promise((resolve) => {
        finish = () => resolve(turn(data))
      })
    },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '问题'
  const pending = chat.send()
  await started
  emit('第一段')
  assert.equal(chat.messages.value.at(-1).content, '第一段')
  assert.equal(chat.messages.value.at(-1).streaming, true)
  assert.equal(chat.canSend.value, false)
  emit('，第二段')
  assert.equal(chat.messages.value.at(-1).content, '第一段，第二段')
  finish()
  await pending
  assert.equal(chat.messages.value.length, 2)
  assert.equal(chat.messages.value.at(-1).content, '已保存的回答')
  assert.equal(Boolean(chat.messages.value.at(-1).streaming), false)
})

test('停止流式生成取消请求，清除未保存片段并从服务端核对状态', async (t) => {
  let submitted, saved, signal, emit
  const started = new Promise((resolve) => {
    submitted = resolve
  })
  const chat = useConversations({
    create: async () => clone(conversation),
    sendStream: (_id, data, currentSignal, onDelta) => {
      signal = currentSignal
      saved = turn(data)
      emit = onDelta
      submitted()
      return new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason)),
      )
    },
    messages: async () => ({
      conversation: { ...conversation, last_sequence: 1 },
      messages: [{ ...saved.user_message, status: 'failed', error_message: '已停止生成' }],
      next_before: null,
    }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = '问题'
  const pending = chat.send()
  await started
  emit('未保存的片段')
  chat.stop()
  await pending
  assert.equal(signal.aborted, true)
  assert.equal(chat.messages.value.length, 1)
  assert.equal(chat.messages.value[0].status, 'failed')
  emit('迟到的片段')
  assert.equal(chat.messages.value.length, 1)
  assert.equal(chat.isLoading.value, false)
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

test('切换会话保留旧请求，后台完成不会串入另一会话', async (t) => {
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
  assert.equal(signal.aborted, false)
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

test('切回仍在生成的会话恢复已有增量，停止只取消当前会话', async (t) => {
  const other = { ...conversation, id: 'b'.repeat(32), title: 'B 对话' }
  const calls = new Map()
  const chat = useConversations({
    create: async () => clone(conversation),
    messages: async (id) => ({
      conversation: clone(id === other.id ? other : conversation),
      messages: [],
      next_before: null,
    }),
    sendStream: (id, data, signal, emit) =>
      new Promise((resolve, reject) => {
        calls.set(id, {
          data,
          signal,
          emit,
          finish: () =>
            resolve({
              ...turn(data),
              conversation: { ...(id === other.id ? other : conversation), last_sequence: 2 },
            }),
        })
        signal.addEventListener('abort', () => reject(signal.reason))
      }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = 'A 问题'
  const a = chat.send()
  await new Promise((resolve) => setImmediate(resolve))
  const runA = calls.get(conversation.id)
  runA.emit('A 的第一段')
  await chat.openConversation(other.id)
  assert.equal(runA.signal.aborted, false)
  chat.draft.value = 'B 问题'
  const b = chat.send()
  await new Promise((resolve) => setImmediate(resolve))
  const runB = calls.get(other.id)
  runB.emit('B 的第一段')
  await chat.openConversation(conversation.id)
  assert.equal(chat.messages.value.at(-1).content, 'A 的第一段')
  assert.equal(chat.isLoading.value, true)
  runA.emit('，第二段')
  assert.equal(chat.messages.value.at(-1).content, 'A 的第一段，第二段')
  chat.stop()
  await a
  assert.equal(runA.signal.aborted, true)
  assert.equal(runB.signal.aborted, false)
  await chat.openConversation(other.id)
  assert.equal(chat.messages.value.at(-1).content, 'B 的第一段')
  runB.finish()
  await b
  assert.equal(chat.isLoading.value, false)
  assert.equal(chat.messages.value.at(-1).content, '已保存的回答')
})

test('新建对话不丢失尚在创建的原问题与附件，随后仍会发送到原会话', async (t) => {
  let resolveCreate, submitted, saved
  const ready = new Promise((resolve) => {
    submitted = resolve
  })
  const chat = useConversations({
    create: () =>
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
    sendStream: async (id, data, signal) => {
      saved = { id, data, signal }
      submitted()
      return turn(data)
    },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '原问题'
  chat.draftAttachments.value = [{ id: 'file-id', kind: 'image', status: 'ready' }]
  const pending = chat.send()
  chat.reset()
  chat.draft.value = '新对话草稿'
  resolveCreate(clone(conversation))
  await pending
  assert.ok(saved, '切换后原问题也必须发送')
  await ready
  assert.equal(saved.signal.aborted, false)
  assert.equal(saved.id, conversation.id)
  assert.deepEqual(saved.data.attachment_ids, ['file-id'])
  assert.equal(chat.current.value, null)
  assert.equal(chat.draft.value, '新对话草稿')
  assert.deepEqual(chat.messages.value, [])
  assert.equal(chat.conversations.value[0].last_sequence, 2)
})

test('退出工作台统一取消后台请求，后台错误切回后可以核对并重试', async (t) => {
  const other = { ...conversation, id: 'b'.repeat(32) }
  let signal, emit, rejectSend, saved
  const chat = useConversations({
    create: async () => clone(conversation),
    sendStream: (_id, data, active, onDelta) =>
      new Promise((_resolve, reject) => {
        signal = active
        emit = onDelta
        saved = turn(data)
        rejectSend = reject
        signal.addEventListener('abort', () => reject(signal.reason))
      }),
    messages: async (id) =>
      id === other.id
        ? { conversation: other, messages: [], next_before: null }
        : {
            conversation: { ...conversation, last_sequence: 1 },
            messages: [{ ...saved.user_message, status: 'failed', error_message: '生成失败' }],
            next_before: null,
          },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '原问题'
  const pending = chat.send()
  await new Promise((resolve) => setImmediate(resolve))
  emit('半段')
  await chat.openConversation(other.id)
  assert.equal(signal.aborted, false)
  rejectSend(new Error('生成失败'))
  await pending
  assert.deepEqual(chat.messages.value, [])
  await chat.openConversation(conversation.id)
  assert.equal(chat.error.value, '生成失败')
  assert.equal(chat.messages.value.length, 1)
  chat.draft.value = '再问'
  chat.editFailed()
  const again = chat.retry()
  await new Promise((resolve) => setImmediate(resolve))
  await chat.openConversation(other.id)
  chat.dispose()
  assert.equal(signal.aborted, true)
  await again
})

test('后台新建失败不能清空新对话草稿', async (t) => {
  let rejectCreate
  const chat = useConversations({
    create: () =>
      new Promise((_resolve, reject) => {
        rejectCreate = reject
      }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = '原问题'
  const pending = chat.send()
  chat.reset()
  chat.draft.value = '新的草稿'
  rejectCreate(new Error('创建失败'))
  await pending
  assert.equal(chat.current.value, null)
  assert.equal(chat.draft.value, '新的草稿')
  assert.equal(chat.notice.value, '')
})

test('离开页面时取消全部会话中的生成请求', async (t) => {
  const other = { ...conversation, id: 'b'.repeat(32) }
  const signals = []
  const chat = useConversations({
    create: async () => clone(conversation),
    messages: async () => ({ conversation: other, messages: [], next_before: null }),
    sendStream: (_id, _data, signal) =>
      new Promise((_resolve, reject) => {
        signals.push(signal)
        signal.addEventListener('abort', () => reject(signal.reason))
      }),
  })
  t.after(() => chat.dispose())
  chat.draft.value = 'A'
  const a = chat.send()
  await new Promise((resolve) => setImmediate(resolve))
  await chat.openConversation(other.id)
  chat.draft.value = 'B'
  const b = chat.send()
  await new Promise((resolve) => setImmediate(resolve))
  chat.dispose()
  assert.deepEqual(
    signals.map((signal) => signal.aborted),
    [true, true],
  )
  await Promise.all([a, b])
})

test('切回生成中的长对话保留历史分页游标，完成后仍能加载更早消息', async (t) => {
  const long = { ...conversation, last_sequence: 100 }
  const other = { ...conversation, id: 'b'.repeat(32) }
  const old = [
    { id: 'old', sequence: 100, role: 'assistant', content: '旧回答', status: 'completed' },
  ]
  let finish, requestedBefore
  const chat = useConversations({
    messages: async (id, before) => {
      if (id === other.id) return { conversation: other, messages: [], next_before: null }
      requestedBefore = before
      return {
        conversation: long,
        messages: before ? [{ ...old[0], id: 'older', sequence: 99 }] : old,
        next_before: before ? null : 100,
      }
    },
    sendStream: (_id, data) =>
      new Promise((resolve) => {
        finish = () => {
          const saved = turn(data)
          resolve({
            ...saved,
            conversation: { ...long, last_sequence: 102 },
            user_message: { ...saved.user_message, sequence: 101 },
            assistant_message: { ...saved.assistant_message, sequence: 102 },
          })
        }
      }),
  })
  t.after(() => chat.dispose())
  await chat.openConversation(conversation.id)
  assert.equal(chat.nextBefore.value, 100)
  chat.draft.value = '继续'
  const pending = chat.send()
  await chat.openConversation(other.id)
  await chat.openConversation(conversation.id)
  assert.equal(chat.nextBefore.value, 100)
  finish()
  await pending
  assert.equal(await chat.loadOlder(), true)
  assert.equal(requestedBefore, 100)
  assert.equal(chat.messages.value[0].id, 'older')
})
