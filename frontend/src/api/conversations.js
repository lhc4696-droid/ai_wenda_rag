import { ApiError, requestJson } from './http.js'
import { streamMessage } from './streaming.js'

async function sendStream(id, data, signal, onDelta) {
  const deadline = AbortSignal.timeout(data.source_mode === 'documents' ? 210000 : 120000)
  const active = signal ? AbortSignal.any([signal, deadline]) : deadline
  try {
    return await streamMessage(`/api/conversations/${id}/messages/stream`, data, active, onDelta)
  } catch (error) {
    if (signal?.aborted) throw error
    if (deadline.aborted) throw new ApiError('等待响应超时，请刷新会话确认保存结果。', 0)
    if (error.status === 401 && typeof window !== 'undefined')
      window.dispatchEvent(new Event('auth-expired'))
    throw error
  }
}

async function historyRequest(path, options = {}, timeout = 15000) {
  const deadline = AbortSignal.timeout(timeout)
  const signal = options.signal ? AbortSignal.any([options.signal, deadline]) : deadline
  try {
    return await requestJson(`/api/conversations${path}`, { ...options, signal })
  } catch (error) {
    if (options.signal?.aborted) throw error
    if (deadline.aborted) throw new ApiError('等待响应超时，请刷新会话确认保存结果。', 0)
    if (error.status === 401 && typeof window !== 'undefined')
      window.dispatchEvent(new Event('auth-expired'))
    throw error
  }
}

export const historyApi = {
  sendStream,
  list: (cursor, signal) =>
    historyRequest(`?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal }),
  create: (id, signal) => historyRequest('', { method: 'POST', body: { id }, signal }),
  messages: (id, before, signal) =>
    historyRequest(`/${id}/messages?limit=50${before ? `&before=${before}` : ''}`, { signal }),
  send: (id, data, signal) =>
    historyRequest(
      `/${id}/messages`,
      { method: 'POST', body: data, signal },
      data.source_mode === 'documents' ? 210000 : 120000,
    ),
  rename: (id, title, signal) =>
    historyRequest(`/${id}`, { method: 'PATCH', body: { title }, signal }),
  remove: (id, signal) => historyRequest(`/${id}`, { method: 'DELETE', signal }),
}
