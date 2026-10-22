// User-run offline regression cases; never contact a real model service.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  getModelSettings,
  saveModelSettings,
  deleteModelSettings,
  testModelConnection,
} from '../src/api/model-settings.js'
import { useConversations } from '../src/composables/useConversations.js'

test('模型配置使用同源认证；保存、关闭和删除不提交其他账号 ID', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options, body: options.body ? JSON.parse(options.body) : null })
    return new Response(JSON.stringify({ enabled: false, has_api_key: false }))
  })
  await getModelSettings()
  await saveModelSettings({
    enabled: true,
    base_url: 'https://models.example.com/v1',
    api_key: 'fake-test-key',
    chat_model: 'chat-example',
    coding_model: '',
    vision_model: '',
  })
  await saveModelSettings({ enabled: false })
  await deleteModelSettings()
  assert.deepEqual(
    requests.map((item) => item.options.method),
    ['GET', 'PATCH', 'PATCH', 'DELETE'],
  )
  for (const item of requests) {
    assert.equal(item.url, '/api/model-settings')
    assert.equal(item.options.credentials, 'same-origin')
    assert.equal(item.options.cache, 'no-store')
    assert.ok(!Object.hasOwn(item.body || {}, 'user_id'))
  }
  assert.deepEqual(requests[2].body, { enabled: false })
  assert.equal(requests[3].body, null)
})

test('执行中的配置冲突保留服务端错误，不当作保存成功', async (t) => {
  t.mock.method(
    globalThis,
    'fetch',
    async () =>
      new Response(JSON.stringify({ detail: '请先停止编程任务' }), {
        status: 409,
        headers: { 'X-Error-Code': 'MODEL_SETTINGS_BUSY' },
      }),
  )
  await assert.rejects(
    () => saveModelSettings({ enabled: false }),
    (error) => error.status === 409 && error.errorCode === 'MODEL_SETTINGS_BUSY',
  )
})

test('连接测试单独提交当前表单，不保存、不启用配置并允许保留原密钥', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options, body: JSON.parse(options.body) })
    return new Response(
      JSON.stringify({
        ok: true,
        target: 'chat',
        model: 'draft-chat',
        elapsed_ms: 42,
        message: '测试通过',
        error_code: null,
        input_tokens: 4,
        output_tokens: 1,
        request_id: 'probe-example',
      }),
    )
  })
  const result = await testModelConnection({
    target: 'chat',
    base_url: 'https://models.example.com/v1',
    chat_model: 'draft-chat',
    coding_model: '',
  })
  assert.equal(result.ok, true)
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, '/api/model-settings/test')
  assert.equal(requests[0].options.method, 'POST')
  assert.equal(requests[0].options.credentials, 'same-origin')
  assert.equal(requests[0].options.cache, 'no-store')
  assert.ok(requests[0].options.signal instanceof AbortSignal)
  assert.ok(!Object.hasOwn(requests[0].body, 'api_key'))
  assert.ok(!Object.hasOwn(requests[0].body, 'enabled'))
  assert.ok(!Object.hasOwn(requests[0].body, 'user_id'))
  assert.ok(!Object.hasOwn(requests[0].body, 'messages'))
})

test('Coding 连接失败作为安全测试结果返回，网络错误不触发自动重试', async (t) => {
  let attempts = 0
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    attempts += 1
    assert.equal(JSON.parse(options.body).target, 'coding')
    return new Response(
      JSON.stringify({
        ok: false,
        target: 'coding',
        model: 'draft-coding',
        elapsed_ms: 80,
        message: '模型未返回符合要求的工具调用',
        error_code: 'MODEL_TEST_TOOLS_UNSUPPORTED',
        input_tokens: null,
        output_tokens: null,
        request_id: 'probe-example',
      }),
    )
  })
  const result = await testModelConnection({
    target: 'coding',
    base_url: 'https://models.example.com/v1',
    api_key: 'fake-test-key',
    chat_model: 'draft-chat',
    coding_model: 'draft-coding',
  })
  assert.equal(result.ok, false)
  assert.equal(result.error_code, 'MODEL_TEST_TOOLS_UNSUPPORTED')
  assert.equal(attempts, 1)
  t.mock.method(globalThis, 'fetch', async () => {
    attempts += 1
    throw new TypeError('offline')
  })
  await assert.rejects(
    () => testModelConnection({ target: 'coding' }),
    (error) => error.status === 0,
  )
  assert.equal(attempts, 2)
})

test('连接测试遵守调用方取消信号，不自动重新发送模型请求', async (t) => {
  const controller = new AbortController()
  let attempts = 0
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    attempts += 1
    controller.abort()
    assert.equal(options.signal.aborted, true)
    options.signal.throwIfAborted()
  })
  await assert.rejects(
    () => testModelConnection({ target: 'chat' }, controller.signal),
    (error) => error.name === 'AbortError',
  )
  assert.equal(attempts, 1)
})

test('切换模型后展示当前配置，过时的能力响应不能覆盖最新结果', async (t) => {
  const resolvers = []
  const chat = useConversations(
    {},
    {
      capabilities: () => new Promise((resolve) => resolvers.push(resolve)),
    },
  )
  t.after(() => chat.dispose())
  chat.messages.value = [{ role: 'assistant', model: 'old-model', content: '旧回复' }]
  const oldRequest = chat.loadCapabilities()
  const latestRequest = chat.loadCapabilities()
  resolvers[1]({ chat_model: 'personal-chat', vision_model: 'personal-vision' })
  await latestRequest
  assert.equal(chat.model.value, 'personal-chat')
  resolvers[0]({ chat_model: 'stale-model' })
  await oldRequest
  assert.equal(chat.model.value, 'personal-chat')
  chat.draftAttachments.value = [{ kind: 'image', status: 'ready' }]
  assert.equal(chat.model.value, 'personal-vision')
})
