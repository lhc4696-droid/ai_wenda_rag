import test from 'node:test'
import assert from 'node:assert/strict'
import { useChat, MAX_MESSAGE_LENGTH, MAX_TURNS } from '../src/composables/useChat.js'
import { sendChat } from '../src/api/chat.js'
import { renderMarkdown } from '../src/lib/markdown.js'

const reply = { role: 'assistant', content: '你好，小明', model: 'qwen-plus' }

test('连续追问携带成功的历史，不把界面消息 ID 发给后端', async () => {
  const calls = []
  const chat = useChat(async (messages) => {
    calls.push(messages)
    return reply
  })
  chat.draft.value = '我叫小明'
  await chat.send()
  chat.draft.value = '我叫什么？'
  await chat.send()
  assert.deepEqual(calls[1], [
    { role: 'user', content: '我叫小明' },
    { role: 'assistant', content: '你好，小明' },
    { role: 'user', content: '我叫什么？' },
  ])
})

test('失败重试不重复添加问题，修改失败问题会恢复输入', async () => {
  let fail = true
  const calls = []
  const chat = useChat(async (messages) => {
    calls.push(messages)
    if (fail) throw new Error('模型暂时不可用')
    return reply
  })
  chat.draft.value = '你好'
  await chat.send()
  assert.equal(chat.error.value, '模型暂时不可用')
  assert.equal(chat.messages.value.length, 1)
  fail = false
  await chat.retry()
  assert.deepEqual(calls[0], calls[1])
  assert.equal(chat.messages.value.length, 2)
  fail = true
  chat.draft.value = '第二个问题'
  await chat.send()
  chat.editFailed()
  assert.equal(chat.draft.value, '第二个问题')
  assert.equal(chat.messages.value.length, 2)
  assert.equal(chat.error.value, '')
})

test('新建对话取消等待，旧回答不会进入新对话', async () => {
  let resolveOld
  let signal
  const chat = useChat((_messages, activeSignal) => {
    signal = activeSignal
    return new Promise((resolve) => {
      resolveOld = resolve
    })
  })
  chat.draft.value = '旧问题'
  const pending = chat.send()
  assert.equal(chat.isLoading.value, true)
  chat.reset()
  assert.equal(signal.aborted, true)
  resolveOld(reply)
  await pending
  assert.equal(chat.messages.value.length, 0)
  assert.equal(chat.isLoading.value, false)
})

test('请求未完成时不能重复发送，超时后可以重试', async () => {
  let calls = 0
  const chat = useChat(() => {
    calls += 1
    return new Promise(() => {})
  }, 15)
  chat.draft.value = '问题'
  const first = chat.send()
  chat.draft.value = '重复请求'
  await chat.send()
  assert.equal(calls, 1)
  await first
  assert.match(chat.error.value, /等待时间较长/)
  assert.equal(chat.isLoading.value, false)
})

test('空消息、超长消息和对话轮数遵守后端边界', async () => {
  let calls = 0
  const chat = useChat(async () => {
    calls += 1
    return reply
  })
  for (const text of ['   ', 'a'.repeat(MAX_MESSAGE_LENGTH + 1)]) {
    chat.draft.value = text
    await chat.send()
  }
  assert.equal(calls, 0)
  for (let i = 0; i < MAX_TURNS; i += 1) {
    chat.draft.value = `问题 ${i}`
    await chat.send()
  }
  chat.draft.value = '超出轮数'
  await chat.send()
  assert.equal(calls, MAX_TURNS)
  assert.equal(chat.limitReached.value, true)
  chat.reset()
  assert.equal(chat.limitReached.value, false)
})

test('请求层保留后端错误，并识别无效返回', async (t) => {
  const mock = t.mock.method(
    globalThis,
    'fetch',
    async () =>
      new Response(JSON.stringify({ detail: '后端尚未配置 DASHSCOPE_API_KEY' }), { status: 503 }),
  )
  await assert.rejects(sendChat([]), /DASHSCOPE_API_KEY/)
  mock.mock.mockImplementation(async () => new Response('Bad Gateway', { status: 502 }))
  await assert.rejects(sendChat([]), /没有返回有效数据/)
  mock.mock.mockImplementation(async () => new Response(JSON.stringify({ content: '' })))
  await assert.rejects(sendChat([]), /有效回答/)
})

test('Markdown 支持代码，禁止原始 HTML 和危险链接', () => {
  const html = renderMarkdown(
    '**你好**\n\n```python\nprint(1)\n```\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))',
  )
  assert.match(html, /<strong>你好<\/strong>/)
  assert.match(html, /<pre><code class="language-python">/)
  assert.doesNotMatch(html, /<script>|href="javascript:/)
  assert.match(renderMarkdown('[资料](https://example.com)'), /rel="noopener noreferrer"/)
  assert.doesNotMatch(renderMarkdown('![图片](https://example.com/test.png)'), /<img/)
})
