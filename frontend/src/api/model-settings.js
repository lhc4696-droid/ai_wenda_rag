import { ApiError, requestJson } from './http.js'

async function modelSettingsRequest(options, path = '/api/model-settings') {
  try {
    return await requestJson(path, options)
  } catch (error) {
    if (error.status === 401 && typeof window !== 'undefined')
      window.dispatchEvent(new Event('auth-expired'))
    throw error
  }
}

export function getModelSettings(signal) {
  return modelSettingsRequest({ signal })
}

export function saveModelSettings(settings) {
  return modelSettingsRequest({ method: 'PATCH', body: settings })
}

export function deleteModelSettings() {
  return modelSettingsRequest({ method: 'DELETE' })
}

export async function testModelConnection(settings, signal) {
  // A probe can take longer than a settings read. The server limits it to 20 seconds.
  const timeoutSignal = AbortSignal.timeout(30000)
  const activeSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal
  try {
    return await modelSettingsRequest(
      { method: 'POST', body: settings, signal: activeSignal },
      '/api/model-settings/test',
    )
  } catch (error) {
    if (timeoutSignal.aborted && !signal?.aborted) throw new ApiError('等待连接测试结果超时。', 0)
    throw error
  }
}
