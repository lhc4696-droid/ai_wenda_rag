import test from 'node:test'
import assert from 'node:assert/strict'
import { renameAccount, changePassword } from '../src/api/auth.js'
import { passwordError, usernameError } from '../src/lib/account-validation.js'
import { useChat } from '../src/composables/useChat.js'

test('改名只提交用户名，改密仍提交旧密码，不提交用户 ID', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) })
    return new Response(JSON.stringify({ message: 'ok' }))
  })
  await renameAccount('new_name')
  await changePassword('abcd1234', 'newpass8')
  assert.deepEqual(requests, [
    { url: '/api/auth/username', body: { username: 'new_name' } },
    {
      url: '/api/auth/password/change',
      body: { current_password: 'abcd1234', new_password: 'newpass8' },
    },
  ])
})

test('账号规则统一接受 8 位密码，拒绝短密码、全空格和无效用户名', () => {
  assert.equal(passwordError('abcd1234'), '')
  assert.ok(passwordError('abc1234'))
  assert.ok(passwordError('        '))
  assert.ok(passwordError('x'.repeat(129)))
  assert.equal(usernameError(' new_name '), '')
  assert.equal(usernameError('ab'), '')
  assert.equal(usernameError('a b'), '')
  assert.ok(usernameError(''))
  assert.ok(usernameError('a\nb'))
})

test('截断说明不混入上下文，超长回答不会破坏后续追问', async () => {
  const calls = []
  const chat = useChat(async (messages) => {
    calls.push(messages)
    return {
      role: 'assistant',
      content: '答'.repeat(25000),
      model: 'test',
      context_trimmed: true,
      answer_truncated: true,
    }
  })
  chat.draft.value = '问题'
  await chat.send()
  assert.equal(chat.messages.value[1].content.length, 20000)
  assert.match(chat.messages.value[1].notice, /最近的完整轮次/)
  chat.draft.value = '继续'
  await chat.send()
  assert.equal(calls[1][1].content.length, 20000)
  assert.ok(!Object.hasOwn(calls[1][1], 'notice'))
  chat.reset()
})

test('限流等待期间不能立即重试，清空对话会清理倒计时', async () => {
  let calls = 0
  const chat = useChat(async () => {
    calls += 1
    throw Object.assign(new Error('请求太频繁'), { status: 429, retryAfter: 60 })
  })
  try {
    chat.draft.value = '问题'
    await chat.send()
    assert.ok(chat.retrySeconds.value > 0)
    await chat.retry()
    assert.equal(calls, 1)
  } finally {
    chat.reset()
  }
  assert.equal(chat.retrySeconds.value, 0)
})
