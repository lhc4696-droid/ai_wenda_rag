export class ApiError extends Error {
  constructor(message, status, retryAfter = 0, requestId = '', errorCode = '') {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
    this.requestId = requestId
    this.errorCode = errorCode
  }
}

export async function requestJson(path, { method = 'GET', body, signal } = {}) {
  let response
  const activeSignal = signal || AbortSignal.timeout(15000)
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: activeSignal,
      cache: 'no-store',
    })
  } catch (error) {
    if (signal?.aborted) throw error
    if (activeSignal.aborted) throw new ApiError('等待服务响应超时，请重试。', 0)
    throw new ApiError('暂时无法连接服务，请检查网络和后端是否已启动。', 0)
  }
  if (response.status === 204) return null
  const requestId = response.headers.get('X-Request-ID') || ''
  const errorCode = response.headers.get('X-Error-Code') || ''
  const failure = (message, retryAfter = 0) => new ApiError(
    response.status >= 500 && requestId ? `${message}（排查编号：${requestId}）` : message,
    response.status, retryAfter, requestId, errorCode,
  )
  let data
  try {
    data = await response.json()
  } catch {
    if (activeSignal.aborted) throw new ApiError('等待服务响应超时，请重试。', 0)
    throw failure('服务没有返回有效数据，请确认 FastAPI 已启动后重试。')
  }
  if (!response.ok) {
    const detail = data?.detail
    const retryAfter = Math.max(0, Number(response.headers.get('Retry-After')) || 0)
    if (typeof detail === 'string') throw failure(detail, retryAfter)
    if (Array.isArray(detail)) {
      const fields = { email: '邮箱', code: '验证码', new_password: '新密码', current_password: '当前密码', password: '密码', username: '用户名' }
      const field = fields[detail[0]?.loc?.at(-1)]
      throw failure(field ? `${field}格式不符合要求，请检查后重试。` : '输入格式不符合要求，请检查表单内容。', retryAfter)
    }
    throw failure(`请求失败（${response.status}），请稍后重试。`, retryAfter)
  }
  return data
}
