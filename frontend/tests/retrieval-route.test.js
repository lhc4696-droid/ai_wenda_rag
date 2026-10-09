import test from 'node:test'
import assert from 'node:assert/strict'
import { decideRetrievalRoute } from '../src/lib/retrieval-route.js'
import { useConversations } from '../src/composables/useConversations.js'

const conversation = { id: 'a'.repeat(32), title: '测试', last_sequence: 0 }

test('明确查资料、普通聊天、模糊指代分别路由', () => {
  assert.equal(decideRetrievalRoute('根据上传的文件回答'), 'documents')
  assert.equal(decideRetrievalRoute('帮我写一首诗'), 'chat')
  assert.equal(decideRetrievalRoute('那它的条件呢'), 'choice')
  assert.equal(decideRetrievalRoute('那它的条件呢', [], true), 'documents')
  assert.equal(decideRetrievalRoute('请总结', [{ kind: 'document' }]), 'documents')
})

test('无文件且问题模糊时先选择；取消不发送，选择聊天才发送', async (t) => {
  const calls = []
  const chat = useConversations({
    create: async () => conversation,
    send: async (_id, data) => {
      calls.push(data)
      return {
        conversation: { ...conversation, last_sequence: 2 },
        user_message: { id: 'u', client_message_id: data.client_message_id.replaceAll('-', ''), sequence: 1, role: 'user', content: data.content },
        assistant_message: { id: 'a', sequence: 2, role: 'assistant', content: '回答' },
      }
    },
  })
  t.after(() => chat.dispose())
  chat.draft.value = '那它该怎么办？'
  await chat.send()
  assert.equal(chat.routeChoice.value, true)
  assert.equal(chat.draft.value, '那它该怎么办？')
  assert.equal(calls.length, 0)
  chat.cancelRouteChoice()
  assert.equal(calls.length, 0)
  await chat.send()
  await chat.chooseRoute('chat')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].source_mode, 'chat')
})

test('显式查资料但新会话没有文档，保留问题并提示上传', async (t) => {
  let creates = 0
  const chat = useConversations({ create: async () => { creates++; return conversation } })
  t.after(() => chat.dispose())
  chat.draft.value = '查一下资料'
  chat.setSourceMode('documents')
  await chat.send()
  assert.equal(creates, 0)
  assert.match(chat.attachmentError.value, /先上传/)
  assert.equal(chat.draft.value, '查一下资料')
})
