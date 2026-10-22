import test from 'node:test'
import assert from 'node:assert/strict'
import { historyApi } from '../src/api/conversations.js'

function responseFor(bytes) {
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of bytes) controller.enqueue(chunk)
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream', 'X-Request-ID': 'request-test' } },
  )
}

test('流式消息保留完整请求契约，按跨包 UTF-8 和 SSE 边界解析', async (t) => {
  const original = globalThis.fetch
  t.after(() => {
    globalThis.fetch = original
  })
  const data = {
    content: '你好',
    client_message_id: 'client-id',
    expected_last_sequence: 0,
    source_mode: 'chat',
  }
  const result = {
    conversation: { id: 'thread' },
    user_message: { id: 'user' },
    assistant_message: { id: 'reply', content: '你好🙂' },
  }
  const text =
    ': heartbeat\r\n\r\ndata: {"type":"start"}\r\n\r\ndata: {"type":"content","delta":"你好🙂"}\r\n\r\ndata: ' +
    JSON.stringify({ type: 'done', result }) +
    '\n\n'
  const bytes = new TextEncoder().encode(text)
  let request
  globalThis.fetch = async (path, options) => {
    request = { path, options }
    return responseFor(Array.from(bytes, (byte) => Uint8Array.of(byte)))
  }
  const deltas = []
  const saved = await historyApi.sendStream('thread', data, undefined, (delta) =>
    deltas.push(delta),
  )
  assert.deepEqual(saved, result)
  assert.deepEqual(deltas, ['你好🙂'])
  assert.equal(request.path, '/api/conversations/thread/messages/stream')
  assert.deepEqual(JSON.parse(request.options.body), data)
  assert.equal(request.options.credentials, 'same-origin')
})

test('没有 done 的断流不可当作已完成回答', async (t) => {
  const original = globalThis.fetch
  t.after(() => {
    globalThis.fetch = original
  })
  globalThis.fetch = async () =>
    responseFor([new TextEncoder().encode('data: {"type":"content","delta":"半段"}\n\n')])
  await assert.rejects(
    historyApi.sendStream('thread', {}, undefined, () => {}),
    /中断|完成|确认/,
  )
})

test('SSE 错误保留限流等待时间及排查编号', async (t) => {
  const original = globalThis.fetch
  t.after(() => {
    globalThis.fetch = original
  })
  globalThis.fetch = async () =>
    responseFor([
      new TextEncoder().encode(
        'data: {"type":"error","message":"额度用完","status":429,"retry_after":30,"error_code":"CHAT_DAILY_LIMIT","request_id":"req-1"}\n\n',
      ),
    ])
  await assert.rejects(historyApi.sendStream('thread', {}, undefined), (error) => {
    assert.equal(error.status, 429)
    assert.equal(error.retryAfter, 30)
    assert.equal(error.requestId, 'req-1')
    assert.equal(error.errorCode, 'CHAT_DAILY_LIMIT')
    return true
  })
})
