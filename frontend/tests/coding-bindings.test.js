import test from 'node:test'
import assert from 'node:assert/strict'
import { createCodingBindings } from '../src/lib/coding-bindings.js'

const conversation = 'aa000000000000000000000000000001'
const otherConversation = 'aa000000000000000000000000000002'

function fixture() {
  const records = new Map()
  const key = (value) => `${value.owner}:${value.conversationId}`
  const storage = {
    read: async (value) => records.get(key(value)),
    put: async (value) => records.set(key(value), { ...value }),
    remove: async (value) => records.delete(key(value)),
    listOwner: async (owner) => [...records.values()].filter((value) => value.owner === owner),
  }
  return { records, storage, bindings: createCodingBindings(storage) }
}

function folder(id, permission = 'granted') {
  return {
    id,
    kind: 'directory',
    name: 'same-name-project',
    permission,
    queries: [],
    async queryPermission(options) {
      this.queries.push(options)
      return this.permission
    },
    async requestPermission() {
      throw new Error('must never request permission silently')
    },
    async isSameEntry(other) {
      return this.id === other.id
    },
  }
}

test('remembered directory can serve repeated turns without a picker or permission request', async () => {
  const { bindings } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  assert.equal(await bindings.get(1, conversation), handle)
  assert.equal(await bindings.get(1, conversation), handle)
  assert.ok(handle.queries.every((options) => options.mode === 'readwrite'))
})

test('bindings cannot cross accounts or conversations', async () => {
  const { bindings } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  assert.equal(await bindings.get(2, conversation), null)
  assert.equal(await bindings.get(1, otherConversation), null)
})

test('permission loss returns no directory and never prompts', async () => {
  const { bindings } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  handle.permission = 'prompt'
  assert.equal(await bindings.get(1, conversation), null)
  await assert.rejects(bindings.save(1, conversation, handle), /权限尚未批准/)
  handle.permission = 'denied'
  assert.equal(await bindings.get(1, conversation), null)
})

test('reconnect checks the actual entry rather than trusting matching names', async () => {
  const { bindings } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  await assert.rejects(bindings.save(1, conversation, folder('project-b')), /不是这个对话/)
  const same = folder('project-a')
  await bindings.save(1, conversation, same)
  assert.equal(await bindings.get(1, conversation), same)
})

test('disconnect disables use while preserving identity for explicit reconnect', async () => {
  const { bindings, records } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  await bindings.forget(1, conversation)
  assert.equal(await bindings.get(1, conversation), null)
  assert.equal(records.size, 1)
  await assert.rejects(bindings.save(1, conversation, folder('project-b')), /不是这个对话/)
  await bindings.save(1, conversation, folder('project-a'))
  assert.ok(await bindings.get(1, conversation))
})

test('logout forgets only the current owner; deleted conversations lose the stored handle', async () => {
  const { bindings, records } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  await bindings.save(1, otherConversation, folder('project-b'))
  await bindings.save(2, conversation, folder('project-c'))
  await bindings.forgetOwner(1)
  assert.equal(records.size, 1)
  assert.equal(await bindings.get(1, conversation), null)
  assert.ok(await bindings.get(2, conversation))
  await bindings.remove(2, conversation)
  assert.equal(records.size, 0)
})

test('cannot verify a previous handle fails closed; no name fallback', async () => {
  const { bindings } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  handle.isSameEntry = async () => {
    throw new Error('stale handle')
  }
  await assert.rejects(bindings.save(1, conversation, folder('project-a')), /无法核对原目录/)
})

test('invalid identity and storage errors are surfaced without saving a usable binding', async () => {
  const { bindings, storage } = fixture()
  await assert.rejects(bindings.get(0, conversation), /有效账号/)
  await assert.rejects(bindings.save(1, '../bad', folder('a')), /有效会话/)
  storage.put = async () => {
    throw new Error('storage unavailable')
  }
  await assert.rejects(bindings.save(1, conversation, folder('a')), /storage unavailable/)
  assert.equal(await bindings.get(1, conversation), null)
})
