// All filesystem access stays in the authorized browser. No absolute path is sent to FastAPI.
export const LOCAL_LIMITS = { files: 150, fileBytes: 128 * 1024, totalBytes: 2 * 1024 * 1024 }
const blockedDirectories = new Set([
  '.git',
  '.svn',
  '.hg',
  '.ssh',
  '.aws',
  '.codex',
  '.agents',
  'venv',
  '.venv',
  'node_modules',
  '__pycache__',
  '.idea',
  '.vscode',
  'dist',
  'build',
  'data',
  '.next',
  '.nuxt',
])
const secretNames = new Set([
  'id_rsa',
  'id_dsa',
  'id_ecdsa',
  'id_ed25519',
  'credentials',
  'credentials.json',
  'secrets.json',
  'secrets.yaml',
  'secrets.yml',
  '.npmrc',
  '.pypirc',
  '.netrc',
])
const secretStems = new Set([
  'api_key',
  'api-key',
  'apikey',
  'secret',
  'secrets',
  'token',
  'access_token',
  'private_key',
  'private-key',
])
const extensions = new Set([
  'py',
  'pyi',
  'vue',
  'svelte',
  'js',
  'jsx',
  'ts',
  'tsx',
  'html',
  'css',
  'scss',
  'less',
  'json',
  'toml',
  'yaml',
  'yml',
  'ini',
  'cfg',
  'md',
  'rst',
  'txt',
  'sql',
  'xml',
  'svg',
  'csv',
  'go',
  'rs',
  'java',
  'kt',
  'kts',
  'c',
  'h',
  'cpp',
  'hpp',
  'cs',
  'sh',
])
const textNames = new Set([
  'dockerfile',
  'containerfile',
  'makefile',
  'license',
  'licence',
  'readme',
  '.gitignore',
  '.dockerignore',
  '.editorconfig',
  'pipfile',
])
const encoder = new TextEncoder()
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })

export function localSupport() {
  return Boolean(
    globalThis.isSecureContext &&
    globalThis.showDirectoryPicker &&
    globalThis.navigator?.locks &&
    globalThis.indexedDB,
  )
}

export function validateLocalPath(path) {
  if (
    typeof path !== 'string' ||
    !path ||
    path.length > 240 ||
    path !== path.normalize('NFC') ||
    /[\\:*?"<>|\u0000-\u001f\u007f]/.test(path) ||
    decoder.decode(encoder.encode(path)) !== path
  )
    throw new Error('文件路径无效，禁止绝对路径或特殊字符。')
  const parts = path.split('/')
  if (
    parts.some(
      (p) =>
        !p ||
        p === '.' ||
        p === '..' ||
        p.length > 100 ||
        /[. ]$/.test(p) ||
        /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i.test(p),
    )
  )
    throw new Error('不能访问授权目录之外的路径。')
  const name = parts.at(-1).toLowerCase()
  if (
    parts.some(
      (p) => blockedDirectories.has(p.toLowerCase()) || p.toLowerCase().startsWith('.env'),
    ) ||
    secretNames.has(name) ||
    secretStems.has(name.replace(/\.[^.]+$/, '')) ||
    (!textNames.has(name) && !extensions.has(name.split('.').at(-1)))
  )
    throw new Error('该目录、敏感文件或非源码类型不允许访问。')
  return path
}

function validText(text) {
  if (
    typeof text !== 'string' ||
    text.includes('\0') ||
    decoder.decode(encoder.encode(text)) !== text ||
    encoder.encode(text).length > LOCAL_LIMITS.fileBytes
  )
    throw new Error('只允许 128 KiB 以内的 UTF-8 文本文件。')
  return text
}

async function readText(handle) {
  const file = await handle.getFile()
  if (file.size > LOCAL_LIMITS.fileBytes) throw new Error('文件超过 128 KiB，未读取。')
  return validText(decoder.decode(await file.arrayBuffer()))
}

async function locate(root, path, createDirectories = false, guard = async () => {}) {
  const parts = validateLocalPath(path).split('/')
  let directory = root
  for (const part of parts.slice(0, -1)) {
    await guard()
    directory = await directory.getDirectoryHandle(part, { create: createDirectories })
  }
  return { directory, name: parts.at(-1) }
}

async function existing(root, path) {
  try {
    const { directory, name } = await locate(root, path)
    return await directory.getFileHandle(name)
  } catch (error) {
    if (error.name === 'NotFoundError') return null
    throw error
  }
}

// Durable before-images only; no directory handles or task permission tokens are persisted.
function journalDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('zhiyan-local-recovery', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('journals', { keyPath: 'key' })
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(new Error('无法打开本地恢复记录，已禁止修改文件。'))
  })
}

async function journalOperation(mode, action) {
  const db = await journalDb()
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction('journals', mode)
      let result
      try {
        const request = action(transaction.objectStore('journals'))
        request.onsuccess = () => {
          result = request.result
        }
      } catch (error) {
        transaction.abort()
        reject(error)
        return
      }
      transaction.oncomplete = () => resolve(result)
      transaction.onerror = transaction.onabort = () =>
        reject(new Error('本地恢复记录保存失败，请检查浏览器存储空间。'))
    })
  } finally {
    db.close()
  }
}

export async function listLocalJournals(owner) {
  return (await journalOperation('readonly', (store) => store.getAll()))
    .filter((item) => item.owner === owner)
    .sort((a, b) => b.createdAt - a.createdAt)
}
export const saveLocalJournal = (journal) =>
  journalOperation('readwrite', (store) => store.put(structuredClone(journal)))
export const deleteLocalJournal = (key) =>
  journalOperation('readwrite', (store) => store.delete(key))

export class LocalFolderSession {
  constructor(root, owner, grantId, onChange = () => {}) {
    this.root = root
    this.active = true
    this.expiresAt = performance.now() + 300000
    this.reads = new Map()
    this.files = new Map()
    this.executed = new Map()
    this.onChange = onChange
    this.journal = {
      key: `${owner}:${grantId}`,
      owner,
      grantId,
      directoryName: root.name,
      createdAt: Date.now(),
      entries: [],
    }
  }

  revoke() {
    this.active = false
    this.reads.clear()
  }

  async allowed() {
    if (performance.now() >= this.expiresAt) this.revoke()
    if (!this.active) throw new Error('本次任务授权已结束。')
    if ((await this.root.queryPermission({ mode: 'readwrite' })) !== 'granted')
      throw new Error('浏览器读写权限已撤销，请重新发起授权。')
    if (!this.active || performance.now() >= this.expiresAt) {
      this.revoke()
      throw new Error('任务已停止或授权已过期。')
    }
  }

  async scan() {
    await this.allowed()
    let visited = 0,
      total = 0,
      ignored = 0
    const found = new Map(),
      folded = new Set()
    const visit = async (directory, prefix = '') => {
      for await (const [name, handle] of directory.entries()) {
        await this.allowed()
        if (++visited > 2500) throw new Error('目录条目过多，请选择更小的项目子目录。')
        const path = prefix + name
        if (handle.kind === 'directory') {
          if (blockedDirectories.has(name.toLowerCase()) || prefix.split('/').length > 12) {
            ignored++
            continue
          }
          // Directory names are validated by validating a harmless leaf beneath them.
          try {
            validateLocalPath(path + '/placeholder.txt')
          } catch {
            ignored++
            continue
          }
          await visit(handle, path + '/')
        } else {
          try {
            validateLocalPath(path)
          } catch {
            ignored++
            continue
          }
          const file = await handle.getFile()
          if (file.size > LOCAL_LIMITS.fileBytes) {
            ignored++
            continue
          }
          if (folded.has(path.toLowerCase()))
            throw new Error('项目含有大小写冲突的路径，请先处理。')
          folded.add(path.toLowerCase())
          found.set(path, { path, size: file.size })
          total += file.size
          if (found.size > LOCAL_LIMITS.files || total > LOCAL_LIMITS.totalBytes)
            throw new Error('项目超出 150 个源码文件或 2 MiB 上限，请选择较小的目录。')
        }
      }
    }
    await visit(this.root)
    this.files = found
    this.onChange()
    return { paths: [...found.keys()].sort(), ignored }
  }

  async read(path) {
    await this.allowed()
    validateLocalPath(path)
    if (!this.files.has(path)) throw new Error('文件不在本次扫描的可访问源码列表中。')
    const handle = await existing(this.root, path)
    if (!handle) throw new Error('文件已不存在，请重新查看项目。')
    const text = await readText(handle)
    await this.allowed()
    this.reads.set(path, text)
    return text
  }

  async write(path, content, creating) {
    await this.allowed()
    validateLocalPath(path)
    validText(content)
    const handle = await existing(this.root, path)
    if (creating && handle) throw new Error('文件已经存在，不能覆盖；请先读取再精确修改。')
    if (!creating && (!handle || !this.reads.has(path)))
      throw new Error('修改前必须先读取现有文件。')
    const before = handle ? await readText(handle) : null
    if (!creating && before !== this.reads.get(path))
      throw new Error('文件已被外部修改；未覆盖，请重新读取再决定修改。')
    if (before === content) return { ok: true, path, unchanged: true }
    if (creating && [...this.files.keys()].some((p) => p.toLowerCase() === path.toLowerCase()))
      throw new Error('文件名大小写冲突。')
    const total =
      [...this.files.values()].reduce((n, f) => n + f.size, 0) -
      (this.files.get(path)?.size || 0) +
      encoder.encode(content).length
    if (
      this.files.size + (creating ? 1 : 0) > LOCAL_LIMITS.files ||
      total > LOCAL_LIMITS.totalBytes
    )
      throw new Error('修改超出本地项目容量限制。')
    const old = this.journal.entries.find((entry) => entry.path === path)
    const entry = {
      path,
      before: old ? old.before : before,
      prior: before,
      after: content,
      phase: 'prepared',
    }
    this.journal.entries = [...this.journal.entries.filter((item) => item.path !== path), entry]
    await saveLocalJournal(this.journal) // Mandatory before the first disk mutation.
    this.onChange()
    await this.allowed()
    const { directory, name } = await locate(this.root, path, creating, () => this.allowed())
    await this.allowed()
    if (creating && (await existing(this.root, path)))
      throw new Error('创建前发现同名文件，已停止以免覆盖。')
    await this.allowed()
    const target = handle || (await directory.getFileHandle(name, { create: true }))
    let stream
    try {
      await this.allowed()
      const expected = before ?? ''
      if ((await readText(target)) !== expected) throw new Error('写入前检测到文件冲突，已停止。')
      await this.allowed()
      stream = await target.createWritable()
      await this.allowed()
      await stream.write(content)
      await this.allowed()
      if ((await readText(target)) !== expected)
        throw new Error('文件在写入期间发生变化，已取消本次保存。')
      await this.allowed()
      await stream.close()
      stream = null
      entry.phase = 'applied'
      this.files.set(path, { path, size: encoder.encode(content).length })
      this.reads.set(path, content)
      await saveLocalJournal(this.journal)
      this.onChange()
      return { ok: true, path, saved_to_local: true }
    } catch (error) {
      if (stream) await stream.abort().catch(() => {})
      this.onChange()
      // Prepared journal deliberately remains: the browser may have closed the
      // stream before its acknowledgement/IndexedDB update was interrupted.
      error.localWriteUncertain = true
      throw error
    }
  }

  async execute(call, round) {
    await this.allowed()
    const key = `${round}:${call.id}`
    const fingerprint = JSON.stringify(call.function)
    const prior = this.executed.get(key)
    if (prior) {
      if (prior.fingerprint !== fingerprint) throw new Error('相同工具编号内容发生变化，已停止。')
      return prior.result
    }
    let result
    try {
      const name = call.function.name
      const args = JSON.parse(call.function.arguments)
      if (!args || typeof args !== 'object' || Array.isArray(args))
        throw new Error('工具参数必须是对象。')
      const fields = {
        list_files: ['prefix'],
        read_file: ['path', 'start_line', 'end_line'],
        search_code: ['query', 'path'],
        write_file: ['path', 'content'],
        replace_text: ['path', 'old_text', 'new_text'],
      }
      if (!fields[name] || Object.keys(args).some((field) => !fields[name].includes(field)))
        throw new Error('当前授权不允许此工具或参数。')
      if (name === 'list_files') {
        if (args.prefix !== undefined && typeof args.prefix !== 'string')
          throw new Error('目录前缀必须是文本。')
        result = {
          ok: true,
          files: [...this.files.keys()].filter((p) => p.startsWith(args.prefix || '')).sort(),
        }
      } else if (name === 'read_file') {
        const text = await this.read(args.path)
        const start = args.start_line ?? 1,
          end = args.end_line ?? start + 199
        if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start)
          throw new Error('行号不正确。')
        const lines = text.split('\n'),
          last = Math.min(end, start + 199, lines.length)
        const content = lines
          .slice(start - 1, last)
          .map((line, i) => `${i + start}: ${line}`)
          .join('\n')
        result = {
          ok: true,
          path: args.path,
          content: content.slice(0, 10000),
          total_lines: lines.length,
          truncated: content.length > 10000 || last < Math.min(end, lines.length),
        }
      } else if (name === 'search_code') {
        if (typeof args.query !== 'string' || !args.query || args.query.length > 200)
          throw new Error('搜索词长度必须为 1–200。')
        const matches = []
        for (const path of args.path ? [args.path] : this.files.keys()) {
          const text = await this.read(path)
          for (const [i, line] of text.split('\n').entries()) {
            const offset = line.indexOf(args.query)
            if (offset >= 0)
              matches.push({
                path,
                line: i + 1,
                text: line.slice(Math.max(0, offset - 60), offset + 180),
              })
            if (matches.length >= 30) break
          }
          if (matches.length >= 30) break
        }
        result = { ok: true, matches, truncated: matches.length >= 30 }
      } else if (name === 'write_file') result = await this.write(args.path, args.content, true)
      else {
        validateLocalPath(args.path)
        const text = this.reads.get(args.path)
        if (text === undefined) throw new Error('必须先读取文件，才能修改。')
        if (
          typeof args.old_text !== 'string' ||
          !args.old_text ||
          typeof args.new_text !== 'string'
        )
          throw new Error('替换文本参数不正确。')
        const first = text.indexOf(args.old_text)
        if (first < 0 || text.indexOf(args.old_text, first + 1) >= 0)
          throw new Error('旧文本必须精确匹配一次，请重新读取并提供唯一上下文。')
        result = await this.write(
          args.path,
          text.slice(0, first) + args.new_text + text.slice(first + args.old_text.length),
          false,
        )
      }
    } catch (error) {
      result = {
        ok: false,
        error: error.message || '浏览器文件操作失败。',
        fatal: Boolean(
          error.localWriteUncertain || !this.active || error.name === 'NotAllowedError',
        ),
      }
    }
    // Bound JSON itself, not only raw content (escaping may multiply its size).
    if (JSON.stringify(result).length > 20000)
      result = { ok: false, error: '返回内容过长，请缩小读取行数或搜索范围。' }
    this.executed.set(key, { fingerprint, result })
    return result
  }
}

export async function restoreLocalJournal(root, journal, signal) {
  if (root.name !== journal.directoryName)
    throw new Error('目录名称与原记录不一致，请重新选择原目录。')
  const restored = [],
    conflicts = []
  const allowed = async () => {
    if (signal?.aborted) throw new Error('恢复已停止。')
    if ((await root.queryPermission({ mode: 'readwrite' })) !== 'granted')
      throw new Error('恢复需要浏览器读写权限。')
    if (signal?.aborted) throw new Error('恢复已停止。')
  }
  for (const entry of [...journal.entries].reverse()) {
    await allowed()
    try {
      validateLocalPath(entry.path)
      const handle = await existing(root, entry.path)
      const current = handle ? await readText(handle) : null
      if (current === entry.before) {
        entry.phase = 'restored'
        restored.push(entry.path)
        continue
      }
      const matches =
        current === entry.after ||
        (entry.phase === 'prepared' &&
          (current === entry.prior || (entry.before === null && current === '')))
      if (!matches) {
        conflicts.push(entry.path)
        continue
      }
      await allowed()
      if (entry.before === null) {
        const { directory, name } = await locate(root, entry.path)
        if ((await readText(handle)) !== current) {
          conflicts.push(entry.path)
          continue
        }
        await allowed()
        await directory.removeEntry(name) // Only the exact new file, never recursive folders.
      } else {
        let stream
        try {
          stream = await handle.createWritable()
          await stream.write(entry.before)
          await allowed()
          if ((await readText(handle)) !== current) throw new Error('恢复前文件已发生变化。')
          await allowed()
          await stream.close()
          stream = null
        } finally {
          if (stream) await stream.abort().catch(() => {})
        }
      }
      entry.phase = 'restored'
      restored.push(entry.path)
    } catch (error) {
      if (signal?.aborted || error.name === 'NotAllowedError') throw error
      conflicts.push(entry.path)
    } finally {
      await saveLocalJournal(journal)
    }
  }
  return { restored, conflicts }
}
