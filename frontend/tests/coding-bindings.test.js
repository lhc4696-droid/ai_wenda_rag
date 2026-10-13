import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createCodingBindings,
  requestCodingDirectoryPermission,
} from '../src/lib/coding-bindings.js'

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

test('bindings cannot cross accounts or projects', async () => {
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
  await assert.rejects(bindings.save(1, conversation, folder('project-b')), /不是这个项目/)
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
  await assert.rejects(bindings.save(1, conversation, folder('project-b')), /不是这个项目/)
  await bindings.save(1, conversation, folder('project-a'))
  assert.ok(await bindings.get(1, conversation))
})

test('logout forgets only the current owner; deleted projects lose the stored handle', async () => {
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
  await assert.rejects(bindings.save(1, '../bad', folder('a')), /有效项目/)
  storage.put = async () => {
    throw new Error('storage unavailable')
  }
  await assert.rejects(bindings.save(1, conversation, folder('a')), /storage unavailable/)
  assert.equal(await bindings.get(1, conversation), null)
})

test('schema v10 legacy project ID reuses the old handle without changing permission', async () => {
  const { bindings, records } = fixture()
  const handle = folder('legacy')
  // v1 storage key is retained; the server migrates project.id = old conversation.id.
  records.set(`1:${conversation}`, {
    owner: 1,
    conversationId: conversation,
    handle,
    enabled: true,
  })
  const first = { id: 'first-dialog', project_id: conversation }
  const second = { id: 'second-dialog', project_id: conversation }
  assert.equal(await bindings.get(1, first.project_id), handle)
  assert.equal(await bindings.get(1, second.project_id), handle)
  await bindings.forget(1, first.project_id)
  assert.equal(await bindings.get(1, second.project_id), null)
  assert.equal(records.size, 1)
})

test('an old disabled binding is never re-enabled by project migration', async () => {
  const { bindings, records } = fixture()
  records.set(`1:${conversation}`, {
    owner: 1,
    conversationId: conversation,
    handle: folder('legacy'),
    enabled: false,
  })
  assert.equal(await bindings.get(1, conversation), null)
  assert.equal(records.get(`1:${conversation}`).enabled, false)
})

test('saved permission-expired directory is only a candidate until explicit browser consent', async () => {
  const { bindings, records } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  handle.permission = 'prompt'
  let permissionRequests = 0
  handle.requestPermission = (options) => {
    ++permissionRequests
    assert.deepEqual(options, { mode: 'readwrite' })
    handle.permission = 'granted'
    return Promise.resolve('granted')
  }
  const candidate = await bindings.prepareReconnect(1, conversation)
  assert.equal(candidate.handle, handle)
  assert.equal(permissionRequests, 0)
  assert.equal(await bindings.get(1, conversation), null)
  // The native call must happen in the click stack, not after storage/lock awaits.
  const permission = requestCodingDirectoryPermission(candidate.handle)
  assert.equal(permissionRequests, 1)
  assert.equal(await permission, 'granted')
  assert.equal(await bindings.reconnect(1, conversation, candidate), handle)
  assert.equal(await bindings.get(1, conversation), handle)
  assert.equal(records.size, 1)
})

test('explicitly disconnected directory can be restored only by explicit reconnection', async () => {
  const { bindings } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  await bindings.forget(1, conversation)
  const candidate = await bindings.prepareReconnect(1, conversation)
  assert.equal(await bindings.get(1, conversation), null)
  assert.equal(await bindings.reconnect(1, conversation, candidate), handle)
  assert.equal(await bindings.get(1, conversation), handle)
})

test('denied permission cannot restore a saved disabled binding', async () => {
  const { bindings, records } = fixture()
  const handle = folder('project-a')
  await bindings.save(1, conversation, handle)
  await bindings.forget(1, conversation)
  handle.permission = 'denied'
  handle.requestPermission = () => Promise.resolve('denied')
  const candidate = await bindings.prepareReconnect(1, conversation)
  assert.equal(await requestCodingDirectoryPermission(candidate.handle), 'denied')
  await assert.rejects(bindings.reconnect(1, conversation, candidate), /尚未允许/)
  assert.equal(records.get(`1:${conversation}`).enabled, false)
  assert.equal(await bindings.get(1, conversation), null)
})

test('a stale permission dialog cannot undo another tab disconnect or logout', async () => {
  const { bindings } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  const old = await bindings.prepareReconnect(1, conversation)
  await bindings.forget(1, conversation)
  await assert.rejects(bindings.reconnect(1, conversation, old), /已变化或已清除/)
  assert.equal(await bindings.get(1, conversation), null)
  const newer = await bindings.prepareReconnect(1, conversation)
  await bindings.forgetOwner(1)
  await assert.rejects(bindings.reconnect(1, conversation, newer), /已变化或已清除/)
  assert.equal(await bindings.prepareReconnect(1, conversation), null)
})

test('reconnection snapshots cannot cross accounts or projects or be reused', async () => {
  const { bindings } = fixture()
  await bindings.save(1, conversation, folder('project-a'))
  const candidate = await bindings.prepareReconnect(1, conversation)
  assert.equal(await bindings.prepareReconnect(2, conversation), null)
  assert.equal(await bindings.prepareReconnect(1, otherConversation), null)
  await assert.rejects(bindings.reconnect(2, conversation, candidate), /已变化或已清除/)
  await assert.rejects(bindings.reconnect(1, otherConversation, candidate), /已变化或已清除/)
  await bindings.reconnect(1, conversation, candidate)
  await assert.rejects(bindings.reconnect(1, conversation, candidate), /已变化或已清除/)
})
