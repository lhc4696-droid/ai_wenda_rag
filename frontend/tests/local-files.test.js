// In-memory browser API substitutes only. No real directories, API calls or models.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  LocalFolderSession,
  validateLocalPath,
  restoreLocalJournal,
} from '../src/lib/local-files.js'

// Both runtimes exercise the same hand-written policy examples.
const pathPolicy = JSON.parse(
  readFileSync(
    new URL('../../backend/tests/fixtures/local_path_policy.json', import.meta.url),
    'utf8',
  ),
)

class MemoryFile {
  kind = 'file'
  saves = 0
  reads = 0
  constructor(name, text = '') {
    this.name = name
    this.text = text
  }
  async getFile() {
    this.reads++
    return new Blob([this.text])
  }
  async createWritable() {
    let pending = ''
    return {
      write: async (text) => {
        pending = text
      },
      close: async () => {
        this.text = pending
        this.saves++
      },
      abort: async () => {},
    }
  }
}
class MemoryDirectory {
  kind = 'directory'
  permission = 'granted'
  children = new Map()
  constructor(name = 'demo') {
    this.name = name
  }
  async queryPermission() {
    return this.permission
  }
  async *entries() {
    yield* this.children
  }
  async getFileHandle(name, { create = false } = {}) {
    if (!this.children.has(name) && create) this.children.set(name, new MemoryFile(name))
    const value = this.children.get(name)
    if (!value) throw new DOMException('Missing', 'NotFoundError')
    if (value.kind !== 'file') throw new DOMException('Directory', 'TypeMismatchError')
    return value
  }
  async getDirectoryHandle(name, { create = false } = {}) {
    if (!this.children.has(name) && create) this.children.set(name, new MemoryDirectory(name))
    const value = this.children.get(name)
    if (!value) throw new DOMException('Missing', 'NotFoundError')
    if (value.kind !== 'directory') throw new DOMException('File', 'TypeMismatchError')
    return value
  }
  async removeEntry(name) {
    this.children.delete(name)
  }
}
function memoryJournals(t) {
  const saved = new Map()
  const old = Object.getOwnPropertyDescriptor(globalThis, 'indexedDB')
  const db = {
    close() {},
    transaction() {
      const transaction = {
        abort() {
          this.onabort?.()
        },
      }
      transaction.objectStore = () => ({
        put(journal) {
          const request = {}
          setTimeout(() => {
            saved.set(journal.key, structuredClone(journal))
            request.result = journal.key
            request.onsuccess?.()
            transaction.oncomplete?.()
          }, 0)
          return request
        },
      })
      return transaction
    },
  }
  Object.defineProperty(globalThis, 'indexedDB', {
    configurable: true,
    value: {
      open() {
        const request = {}
        queueMicrotask(() => {
          request.result = db
          request.onsuccess?.()
        })
        return request
      },
    },
  })
  t.after(() => {
    if (old) Object.defineProperty(globalThis, 'indexedDB', old)
    else delete globalThis.indexedDB
  })
  return saved
}
function call(name, args, id = name) {
  return { id, function: { name, arguments: JSON.stringify(args) } }
}

test('前后端共用路径用例：敏感路径拦截，正常源码保持可用', () => {
  for (const path of pathPolicy.blocked)
    assert.throws(() => validateLocalPath(path), undefined, path)
  for (const path of pathPolicy.allowed) assert.equal(validateLocalPath(path), path)
})

test('敏感目录不能扫描、读取、写入或通过恢复记录访问', async (t) => {
  memoryJournals(t)
  for (const path of [
    '.docker/config.json',
    '.kube/config.yaml',
    'nested/.DOCKER/config.json',
    'nested/tools/.KuBe/config.yaml',
    '.codex/config.toml',
    '.agents/instructions.md',
  ]) {
    const root = new MemoryDirectory()
    root.children.set('main.py', new MemoryFile('main.py', 'pass'))
    const parts = path.split('/')
    let directory = root
    for (const name of parts.slice(0, -1)) {
      const child = new MemoryDirectory(name)
      directory.children.set(name, child)
      directory = child
    }
    const file = new MemoryFile(parts.at(-1), 'sensitive test fixture')
    directory.children.set(file.name, file)
    const session = new LocalFolderSession(root, 1, path)
    assert.deepEqual((await session.scan()).paths, ['main.py'], path)
    // A stale or injected manifest must not bypass checks at the access boundary.
    session.files.set(path, { path, size: 22 })
    session.reads.set(path, file.text)
    await assert.rejects(session.read(path))
    await assert.rejects(session.write(path, 'changed', false))
    await assert.rejects(session.write(path, 'created', true))
    assert.equal(
      (await session.execute(call('search_code', { query: 'sensitive', path }), 1)).ok,
      false,
    )
    assert.equal(
      (
        await session.execute(
          call('replace_text', {
            path,
            old_text: 'sensitive',
            new_text: 'changed',
          }),
          2,
        )
      ).ok,
      false,
    )
    const restored = await restoreLocalJournal(root, {
      key: `1:${path}`,
      directoryName: root.name,
      entries: [{ path, before: 'original', after: file.text, phase: 'applied' }],
    })
    assert.deepEqual(restored, { restored: [], conflicts: [path] })
    assert.equal(file.reads, 0, path)
    assert.equal(file.saves, 0, path)
    assert.equal(file.text, 'sensitive test fixture')
  }
})

test('拒绝越界、敏感文件、非法 Unicode 和非源码', () => {
  for (const path of [
    '../a.py',
    '/a.py',
    'C:/a.py',
    'src\\a.py',
    '.env',
    '.env.local/key.txt',
    '.git/config',
    'src/secrets.json',
    'token.txt',
    'image.png',
    'con.py',
    '\ud800.py',
  ])
    assert.throws(() => validateLocalPath(path))
  for (const path of ['src/App.vue', 'src/main.kt', 'main.py', '.gitignore'])
    assert.equal(validateLocalPath(path), path)
})

test('先读后写、幂等执行、恢复修改；不使用实际磁盘', async (t) => {
  const saved = memoryJournals(t)
  const root = new MemoryDirectory()
  const file = new MemoryFile('main.py', 'value = 1\n')
  root.children.set(file.name, file)
  const session = new LocalFolderSession(root, 1, 'test')
  await session.scan()
  const change = call('replace_text', { path: 'main.py', old_text: '1', new_text: '2' })
  assert.equal((await session.execute(change, 1)).ok, false)
  await session.execute(call('read_file', { path: 'main.py' }), 2)
  assert.equal((await session.execute(change, 3)).ok, true)
  assert.equal((await session.execute(change, 3)).ok, true)
  assert.equal(file.text, 'value = 2\n')
  assert.equal(file.saves, 1)
  assert.equal(saved.get('1:test').entries[0].before, 'value = 1\n')
  assert.equal(
    (await session.execute(call('write_file', { path: 'new.py', content: 'pass' }), 4)).ok,
    true,
  )
  const restored = await restoreLocalJournal(root, session.journal)
  assert.deepEqual(restored.conflicts, [])
  assert.equal(file.text, 'value = 1\n')
  assert.equal(root.children.has('new.py'), false)
})

test('外部修改不覆盖；撤销和超时阻止后续操作', async (t) => {
  memoryJournals(t)
  const root = new MemoryDirectory()
  const file = new MemoryFile('main.py', 'value = 1\n')
  root.children.set(file.name, file)
  const session = new LocalFolderSession(root, 1, 'test')
  await session.scan()
  await session.read('main.py')
  file.text = 'user edit\n'
  const result = await session.execute(
    call('replace_text', { path: 'main.py', old_text: '1', new_text: '2' }),
    1,
  )
  assert.equal(result.ok, false)
  assert.equal(file.text, 'user edit\n')
  assert.equal(file.saves, 0)
  session.expiresAt = 0
  await assert.rejects(session.read('main.py'))
  await assert.rejects(session.execute(call('write_file', { path: 'bad.py', content: 'pass' }), 2))
  assert.equal(root.children.has('bad.py'), false)
})

test('备份之后撤销授权不会创建文件；模型不能运行命令', async (t) => {
  const saved = memoryJournals(t)
  const root = new MemoryDirectory()
  const session = new LocalFolderSession(root, 1, 'test')
  await session.scan()
  assert.equal((await session.execute(call('shell', { command: 'anything' }), 1)).ok, false)
  assert.equal((await session.execute(call('delete_file', { path: 'a.py' }), 1)).ok, false)
  session.onChange = () => session.revoke()
  const result = await session.execute(
    call('write_file', { path: 'src/new.py', content: 'pass' }),
    2,
  )
  assert.equal(result.ok, false)
  assert.equal(result.fatal, true)
  assert.equal(root.children.size, 0)
  assert.equal(saved.get('1:test').entries[0].phase, 'prepared')
})

test('恢复跳过后续修改，不相信目录名就覆盖', async (t) => {
  memoryJournals(t)
  const root = new MemoryDirectory()
  root.children.set('main.py', new MemoryFile('main.py', 'my latest edit'))
  const journal = {
    key: '1:test',
    directoryName: 'demo',
    entries: [{ path: 'main.py', before: 'original', after: 'model output', phase: 'applied' }],
  }
  const result = await restoreLocalJournal(root, journal)
  assert.deepEqual(result.conflicts, ['main.py'])
  assert.equal(root.children.get('main.py').text, 'my latest edit')
  await assert.rejects(restoreLocalJournal(new MemoryDirectory('wrong-folder'), journal))
})
