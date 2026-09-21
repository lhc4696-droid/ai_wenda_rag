import { randomUUID } from 'node:crypto'

// 仅用于浏览器界面回归：模拟持久化接口，不访问真实数据库或模型。
export async function mockHistory(
  page,
  generate = (route) => route.fulfill({ json: { content: '模拟回答', model: 'test' } }),
  attachments = new Map(),
) {
  await page.route('**/api/attachments/capabilities', (route) =>
    route.fulfill({
      json: {
        extensions: ['.jpg', '.jpeg', '.png', '.webp', '.txt', '.md', '.pdf', '.docx'],
        max_files: 3,
        max_bytes: 10485760,
        images_enabled: true,
        vision_model: 'test-vision',
      },
    }),
  )
  const conversations = new Map()
  const messages = new Map()
  let clock = 1800000000
  await page.route('**/api/conversations**', async (route) => {
    const url = new URL(route.request().url())
    const [id, child] = url.pathname.replace('/api/conversations', '').split('/').filter(Boolean)
    const method = route.request().method()
    const body = method === 'POST' || method === 'PATCH' ? route.request().postDataJSON() : null
    const fulfill = (options) => route.fulfill(options).catch(() => {})
    const failure = (status, detail, code = '') =>
      fulfill({ status, json: { detail }, headers: { 'X-Error-Code': code } })
    if (!id && method === 'POST') {
      const createdId = body.id.replaceAll('-', '')
      if (!conversations.has(createdId)) {
        conversations.set(createdId, {
          id: createdId,
          title: '新的对话',
          created_at: ++clock,
          updated_at: clock,
          last_sequence: 0,
        })
        messages.set(createdId, [])
      }
      return fulfill({ status: 201, json: conversations.get(createdId) })
    }
    if (!id && method === 'GET') {
      const offset = Number(url.searchParams.get('cursor') || 0)
      const items = [...conversations.values()].sort((a, b) => b.updated_at - a.updated_at)
      return fulfill({
        json: {
          items: items.slice(offset, offset + 20),
          next_cursor: items.length > offset + 20 ? String(offset + 20) : null,
        },
      })
    }
    const conversation = conversations.get(id)
    if (!conversation) return failure(404, '会话不存在或无法访问')
    const history = messages.get(id)
    if (child === 'messages' && method === 'GET') {
      const before = Number(url.searchParams.get('before') || conversation.last_sequence + 1)
      const eligible = history.filter((message) => message.sequence < before)
      const selected = eligible.slice(-50)
      return fulfill({
        json: {
          conversation,
          messages: selected,
          next_before: eligible.length > 50 ? selected[0].sequence : null,
        },
      })
    }
    if (method === 'PATCH') {
      conversation.title = body.title.trim()
      return fulfill({ json: conversation })
    }
    if (method === 'DELETE') {
      if (history.at(-1)?.status === 'pending') return failure(409, '会话仍在回答中')
      conversations.delete(id)
      messages.delete(id)
      return fulfill({ status: 204 })
    }
    if (child !== 'messages' || method !== 'POST') return failure(405, '不支持此操作')
    const clientId = body.client_message_id.replaceAll('-', '')
    let user = history.find(
      (message) => message.role === 'user' && message.client_message_id === clientId,
    )
    if (user?.status === 'completed') {
      const answer = history.find(
        (message) => message.role === 'assistant' && message.client_message_id === clientId,
      )
      return fulfill({
        json: { conversation, user_message: user, assistant_message: answer, replayed: true },
      })
    }
    if (user?.status === 'pending') return failure(409, '回答正在处理中', 'TURN_PENDING')
    if (!user && body.expected_last_sequence !== conversation.last_sequence)
      return failure(409, '会话已更新', 'STALE_CONVERSATION')
    if (!user) {
      user = {
        id: randomUUID(),
        client_message_id: clientId,
        role: 'user',
        content: body.content,
        attachments: (body.attachment_ids || []).map((id) => attachments.get(id)).filter(Boolean),
        sequence: ++conversation.last_sequence,
        status: 'pending',
        created_at: ++clock,
      }
      history.push(user)
      if (conversation.last_sequence === 1) conversation.title = body.content.slice(0, 30)
    }
    user.status = 'pending'
    conversation.updated_at = ++clock
    return generate({
      request: () => route.request(),
      fulfill: async ({ status = 200, json, headers }) => {
        if (status >= 400) {
          user.status = 'failed'
          user.error_message = json.detail
          return fulfill({ status, json, headers })
        }
        user.status = 'completed'
        const answer = {
          ...json,
          id: randomUUID(),
          client_message_id: clientId,
          role: 'assistant',
          sequence: ++conversation.last_sequence,
          status: 'completed',
          created_at: ++clock,
        }
        history.push(answer)
        conversation.updated_at = ++clock
        return fulfill({
          json: { conversation, user_message: user, assistant_message: answer, replayed: false },
        })
      },
    })
  })
  return { conversations, messages }
}
