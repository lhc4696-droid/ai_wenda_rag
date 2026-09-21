import test from 'node:test'
import assert from 'node:assert/strict'
import { useConversations } from '../src/composables/useConversations.js'

const file = {
  id: 'f'.repeat(32),
  filename: 'notes.txt',
  kind: 'document',
  status: 'ready',
  size: 4,
}
const capabilities = { extensions: ['.txt', '.png'], max_bytes: 10485760, images_enabled: false }
const conversation = { id: 'a'.repeat(32), title: '附件会话', last_sequence: 0 }
function create(api = {}, files = {}) {
  const chat = useConversations(
    { create: async () => conversation, ...api },
    { remove: async () => {}, ...files },
  )
  chat.attachmentCapabilities.value = capabilities
  return chat
}

test('附件未就绪禁止发送，成功后仅发送附件 ID', async (t) => {
  let release, submitted
  const pending = new Promise((resolve) => {
    release = resolve
  })
  const chat = create(
    {
      send: async (id, data) => {
        submitted = data
        return {
          conversation,
          user_message: {
            id: 'u',
            role: 'user',
            content: data.content,
            attachments: [file],
            client_message_id: data.client_message_id,
            sequence: 1,
          },
          assistant_message: { id: 'a', role: 'assistant', content: '完成', sequence: 2 },
        }
      },
    },
    {
      upload: async () => {
        await pending
        return file
      },
    },
  )
  t.after(() => chat.dispose())
  chat.draft.value = '总结附件'
  const uploading = chat.addFiles([{ name: 'notes.txt', size: 4 }])
  assert.equal(chat.canSend.value, false)
  release()
  await uploading
  assert.equal(chat.canSend.value, true)
  await chat.send()
  assert.deepEqual(submitted.attachment_ids, [file.id])
  assert.equal(Object.hasOwn(submitted, 'attachments'), false)
  assert.equal(chat.draftAttachments.value.length, 0)
  assert.equal(chat.messages.value[0].attachments[0].id, file.id)
})

test('切换会话丢弃迟到附件并清理，不串入新会话', async (t) => {
  let release
  const pending = new Promise((resolve) => {
    release = resolve
  })
  const removed = []
  const chat = create(
    {},
    {
      upload: async () => {
        await pending
        return file
      },
      remove: async (id) => removed.push(id),
    },
  )
  t.after(() => chat.dispose())
  const uploading = chat.addFiles([{ name: 'notes.txt', size: 4 }])
  chat.reset()
  release()
  await uploading
  assert.equal(chat.draftAttachments.value.length, 0)
  assert.deepEqual(removed, [file.id])
})

test('解析失败与未启用图片有明确反馈', async (t) => {
  const chat = create(
    {},
    { upload: async () => ({ ...file, status: 'failed', error_message: '文档过长' }) },
  )
  t.after(() => chat.dispose())
  chat.draft.value = '总结'
  await chat.addFiles([{ name: 'image.png', size: 4 }])
  assert.match(chat.attachmentError.value, /视觉模型/)
  await chat.addFiles([{ name: 'notes.txt', size: 4 }])
  assert.equal(chat.canSend.value, false)
  chat.removeAttachment(chat.draftAttachments.value[0])
  assert.equal(chat.canSend.value, true)
})

test('发送响应丢失且问题未保存时恢复附件草稿', async (t) => {
  const chat = create({
    send: async () => {
      throw new Error('断网')
    },
    messages: async () => ({ conversation, messages: [], next_before: null }),
  })
  t.after(() => chat.dispose())
  chat.draftAttachments.value = [file]
  chat.draft.value = '请总结'
  await chat.send()
  assert.equal(chat.draft.value, '请总结')
  assert.equal(chat.draftAttachments.value[0].id, file.id)
  assert.match(chat.notice.value, /断网/)
  assert.match(chat.notice.value, /已放回输入框/)
})
