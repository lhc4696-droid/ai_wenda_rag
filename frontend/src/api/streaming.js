import { ApiError } from './http.js'

// POST + Cookie authentication, with framing independent of network chunk boundaries.
export async function streamMessage(path, body, signal, onDelta = () => {}) {
  let response, reader
  try {
    response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    })
    const requestId = response.headers.get('X-Request-ID') || ''
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new ApiError(
        typeof failure?.detail === 'string'
          ? failure.detail
          : `请求失败（${response.status}），请刷新后重试。`,
        response.status,
        Number(response.headers.get('Retry-After')) || 0,
        requestId,
        response.headers.get('X-Error-Code') || '',
      )
    }
    if (!response.headers.get('Content-Type')?.includes('text/event-stream') || !response.body)
      throw new ApiError('服务未返回流式响应，请确认前后端版本一致。', 0, 0, requestId)
    reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let buffer = '',
      dataLines = [],
      eventSize = 0

    function eventFor(line) {
      if (line === '') {
        const data = dataLines.join('\n')
        dataLines = []
        eventSize = 0
        if (!data) return null
        try {
          return JSON.parse(data)
        } catch {
          throw new ApiError('流式响应格式无效，请刷新会话确认保存结果。', 0, 0, requestId)
        }
      }
      if (line.startsWith('data:')) {
        const data = line.slice(5).replace(/^ /, '')
        eventSize += data.length
        if (eventSize > 1_000_000) throw new ApiError('流式响应过大，请刷新会话确认。', 0)
        dataLines.push(data)
      }
      return null
    }

    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let end
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, '')
        buffer = buffer.slice(end + 1)
        const event = eventFor(line)
        if (!event) continue
        if (event.type === 'error')
          throw new ApiError(
            event.message || '生成失败，请刷新后重试。',
            event.status || 503,
            event.retry_after || 0,
            event.request_id || requestId,
            event.error_code || '',
          )
        if (event.type === 'content') {
          if (typeof event.delta !== 'string')
            throw new ApiError('流式文本格式无效，请刷新会话。', 0)
          onDelta(event.delta)
        }
        if (event.type === 'done') {
          if (
            !event.result?.conversation ||
            !event.result?.user_message ||
            !event.result?.assistant_message
          )
            throw new ApiError('回答保存结果无效，请刷新会话确认。', 0)
          return event.result
        }
      }
      if (buffer.length > 1_000_000) throw new ApiError('流式响应过大，请刷新会话确认。', 0)
      if (done)
        throw new ApiError(
          '回答连接已中断，尚未确认完成，请刷新会话确认保存结果。',
          0,
          0,
          requestId,
        )
    }
  } catch (error) {
    if (signal?.aborted) throw signal.reason || error
    if (error instanceof ApiError) throw error
    throw new ApiError('回答连接已中断，请刷新会话确认保存结果。', 0)
  } finally {
    if (reader) {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  }
}
