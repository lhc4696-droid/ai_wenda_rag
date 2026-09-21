import { ApiError, requestJson } from './http.js'

async function authenticated(task) {
  try {
    return await task()
  } catch (error) {
    if (error.status === 401) window.dispatchEvent(new Event('auth-expired'))
    throw error
  }
}

export const attachmentApi = {
  capabilities: (signal) =>
    authenticated(() =>
      requestJson('/api/attachments/capabilities', {
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : undefined,
      }),
    ),
  remove: (id) => requestJson(`/api/attachments/${id}`, { method: 'DELETE' }),
  upload: (file, signal) =>
    authenticated(async () => {
      const form = new FormData()
      form.append('file', file)
      const deadline = AbortSignal.timeout(90000)
      const active = AbortSignal.any([signal, deadline])
      let response
      try {
        response = await fetch('/api/attachments', {
          method: 'POST',
          credentials: 'same-origin',
          body: form,
          signal: active,
          cache: 'no-store',
        })
      } catch (error) {
        if (signal.aborted) throw error
        throw new ApiError(
          deadline.aborted ? '上传处理超时，请移除后重试。' : '上传连接失败，请检查网络后重试。',
          0,
        )
      }
      const data = await response.json().catch(() => null)
      if (!response.ok)
        throw new ApiError(
          typeof data?.detail === 'string' ? data.detail : '附件上传失败，请检查文件后重试。',
          response.status,
        )
      if (!data?.id) throw new ApiError('附件返回格式异常，请重试。', 0)
      return data
    }),
}

export function attachmentUrl(id) {
  return /^[a-f0-9]{32}$/.test(id || '') ? `/api/attachments/${id}/content` : ''
}
