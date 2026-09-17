import { requestJson } from './http.js'

export function currentUser() {
  return requestJson('/api/auth/me')
}

export function signIn(username, password) {
  return requestJson('/api/auth/login', { method: 'POST', body: { username, password } })
}

export function signUp(username, password, email, code) {
  return requestJson('/api/auth/register', {
    method: 'POST',
    body: { username, password, email, code },
  })
}

export function sendEmailCode(email, purpose) {
  return requestJson('/api/auth/email-code', { method: 'POST', body: { email, purpose } })
}

export function resetPassword(email, code, newPassword) {
  return requestJson('/api/auth/password/reset', {
    method: 'POST',
    body: { email, code, new_password: newPassword },
  })
}

export function signOut() {
  return requestJson('/api/auth/logout', { method: 'POST', body: {} })
}

async function accountRequest(path, options) {
  try {
    return await requestJson(path, options)
  } catch (error) {
    if (error.status === 401 && typeof window !== 'undefined')
      window.dispatchEvent(new Event('auth-expired'))
    throw error
  }
}

export function renameAccount(username) {
  return accountRequest('/api/auth/username', { method: 'POST', body: { username } })
}

export function changePassword(currentPassword, newPassword) {
  return accountRequest('/api/auth/password/change', {
    method: 'POST',
    body: { current_password: currentPassword, new_password: newPassword },
  })
}

export function accountUsage() {
  return accountRequest('/api/chat/usage')
}
