import test from 'node:test'
import assert from 'node:assert/strict'
import { resetPassword, sendEmailCode, signIn, signUp } from '../src/api/auth.js'
import { requestJson } from '../src/api/http.js'

test('注册增加邮箱验证码，登录仍只提交用户名和密码', async () => {
  const previous = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options, body: JSON.parse(options.body) })
    return new Response(JSON.stringify({ user: { id: 1, username: 'tester' } }), { status: 200 })
  }
  try {
    await signUp('tester', 'Example-password!', 'tester@example.com', '003821')
    await signIn('tester', 'Example-password!')
    assert.deepEqual(requests[0].body, { username: 'tester', password: 'Example-password!', email: 'tester@example.com', code: '003821' })
    assert.deepEqual(requests[1].body, { username: 'tester', password: 'Example-password!' })
    assert.equal(requests[0].options.credentials, 'same-origin')
  } finally { globalThis.fetch = previous }
})

test('发码明确用途，重置密码提交独立的新密码字段', async () => {
  const previous = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) })
    return new Response(JSON.stringify({ message: '已受理' }), { status: 200 })
  }
  try {
    await sendEmailCode('tester@example.com', 'reset_password')
    await resetPassword('tester@example.com', '003821', 'Replacement-password!')
    assert.deepEqual(requests[0], { url: '/api/auth/email-code', body: { email: 'tester@example.com', purpose: 'reset_password' } })
    assert.deepEqual(requests[1], { url: '/api/auth/password/reset', body: { email: 'tester@example.com', code: '003821', new_password: 'Replacement-password!' } })
  } finally { globalThis.fetch = previous }
})

test('限流保留服务端 Retry-After，校验错误不转发输入内容', async () => {
  const previous = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '请稍后重试' }), { status: 429, headers: { 'Retry-After': '90' } })
    await assert.rejects(() => requestJson('/api/auth/email-code'), (error) => error.status === 429 && error.retryAfter === 90)
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ loc: ['body', 'code'], msg: 'bad input', input: 'secret-value' }] }), { status: 422 })
    await assert.rejects(() => requestJson('/api/auth/password/reset'), (error) => error.message.includes('验证码') && !error.message.includes('secret-value'))
  } finally { globalThis.fetch = previous }
})
