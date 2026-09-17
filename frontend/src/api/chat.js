import { requestJson } from './http.js'

// 浏览器通过 HttpOnly Cookie 携带本站登录态；千问密钥仍仅在后端。
export async function sendChat(messages, signal) {
  let data
  try {
    data = await requestJson('/api/chat', { method: 'POST', body: { messages }, signal })
  } catch (error) {
    if (error.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('auth-expired'))
    }
    throw error
  }
  if (data?.role !== 'assistant' || typeof data.content !== 'string' || !data.content.trim()) {
    throw new Error('AI 暂时没有给出有效回答，请重试。')
  }
  return data
}

export async function checkHealth() {
  try {
    const data = await requestJson('/api/health', { signal: AbortSignal.timeout(5000) })
    return data?.status === 'ok'
  } catch {
    return false
  }
}
