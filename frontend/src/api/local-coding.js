import { requestJson } from './http.js'

const base = '/api/coding/local/tasks'
const path = (id) => `${base}/${encodeURIComponent(id)}`
const conversations = '/api/coding/local/conversations'
const conversationPath = (id) => `${conversations}/${encodeURIComponent(id)}`
export const localCodingApi = {
  listConversations: (signal) => requestJson(conversations, { signal }),
  createConversation: (body, signal) =>
    requestJson(conversations, { method: 'POST', body, signal }),
  getConversation: (id, signal) => requestJson(conversationPath(id), { signal }),
  removeConversation: (id, signal) =>
    requestJson(conversationPath(id), { method: 'DELETE', signal }),
  capabilities: (signal) => requestJson('/api/coding/local/capabilities', { signal }),
  list: (signal) => requestJson(base, { signal }),
  create: (body, signal) => requestJson(base, { method: 'POST', body, signal }),
  get: (id, signal) => requestJson(path(id), { signal }),
  results: (id, body, signal) =>
    requestJson(`${path(id)}/results`, { method: 'POST', body, signal }),
  cancel: (id, signal) => requestJson(`${path(id)}/cancel`, { method: 'POST', signal }),
  remove: (id, signal) => requestJson(path(id), { method: 'DELETE', signal }),
  stopOnLeave(id) {
    return fetch(`${path(id)}/cancel`, {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
    }).catch(() => {})
  },
}
