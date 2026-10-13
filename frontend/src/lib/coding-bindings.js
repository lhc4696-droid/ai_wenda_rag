// Directory capabilities are keyed by (owner, project ID), never by the active conversation.
// The persisted keyPath stays "conversationId" for compatibility with IndexedDB v1.
// Schema v10 gives every legacy project the old conversation ID, so no capability is
// copied, merged by folder name, or silently re-authorized during the upgrade.
// Restoring a remembered handle checks permission, but never requests it or starts a task.
function identity(owner, projectId) {
  if (!Number.isSafeInteger(owner) || owner <= 0) throw new Error('目录绑定缺少有效账号。')
  const id = String(projectId).replaceAll('-', '').toLowerCase()
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('目录绑定缺少有效项目。')
  return { owner, conversationId: id }
}

function directory(handle) {
  if (handle?.kind !== 'directory' || typeof handle.queryPermission !== 'function')
    throw new Error('浏览器没有提供有效的文件夹访问句柄。')
  return handle
}

async function granted(handle) {
  try {
    return (await directory(handle).queryPermission({ mode: 'readwrite' })) === 'granted'
  } catch {
    return false
  }
}

async function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('zhiyan-coding-bindings', 1)
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore('bindings', {
        keyPath: ['owner', 'conversationId'],
      })
      store.createIndex('owner', 'owner')
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(new Error('无法打开项目目录绑定，请检查浏览器存储权限。'))
  })
}

async function operation(mode, action) {
  const db = await openDatabase()
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction('bindings', mode)
      let result
      try {
        const request = action(transaction.objectStore('bindings'))
        request.onsuccess = () => {
          result = request.result
        }
      } catch {
        transaction.abort()
        reject(new Error('无法保存项目目录绑定，本次连接未完成。'))
        return
      }
      transaction.oncomplete = () => resolve(result)
      transaction.onerror = transaction.onabort = () =>
        reject(new Error('项目目录绑定存储失败，请检查浏览器存储空间。'))
    })
  } finally {
    db.close()
  }
}

const browserStorage = {
  read: (key) => operation('readonly', (store) => store.get([key.owner, key.conversationId])),
  put: (value) => operation('readwrite', (store) => store.put(value)),
  remove: (key) => operation('readwrite', (store) => store.delete([key.owner, key.conversationId])),
  listOwner: (owner) => operation('readonly', (store) => store.index('owner').getAll(owner)),
}

// Serialize changes across tabs so two connections cannot silently replace each other's root.
const browserLock = (action) => navigator.locks.request('zhiyan-coding-bindings', action)

// Call directly from a user click, before awaiting storage, locks or network.
// This requests browser permission only; it never enables an app binding by itself.
export function requestCodingDirectoryPermission(handle) {
  directory(handle)
  if (typeof handle.requestPermission !== 'function')
    throw new Error('当前浏览器无法恢复目录授权，请重新选择原目录。')
  return handle.requestPermission({ mode: 'readwrite' })
}

export function createCodingBindings(storage, lock = (action) => action()) {
  return {
    async prepareReconnect(owner, projectId) {
      const key = identity(owner, projectId)
      return lock(async () => {
        const row = await storage.read(key)
        if (!row) return null
        try {
          directory(row.handle)
          if (typeof row.handle.requestPermission !== 'function') return null
        } catch {
          return null
        }
        // A candidate is NOT an authorized execution handle. Only the explicit button
        // may request permission, then commit this snapshot if it is still current.
        return { ...key, handle: row.handle, revision: row.revision ?? row.updatedAt }
      })
    },
    async reconnect(owner, projectId, candidate) {
      const key = identity(owner, projectId)
      return lock(async () => {
        const row = await storage.read(key)
        if (
          !candidate ||
          candidate.owner !== owner ||
          candidate.conversationId !== key.conversationId ||
          !row ||
          (row.revision ?? row.updatedAt) !== candidate.revision
        )
          throw new Error('目录记录已变化或已清除，请关闭提示后重新连接。')
        if (!(await directory(row.handle).isSameEntry(directory(candidate.handle))))
          throw new Error('保存的目录与本次授权不匹配，未恢复访问。')
        if (!(await granted(candidate.handle)))
          throw new Error('浏览器尚未允许目录读写，未执行文件操作。')
        await storage.put({
          ...row,
          enabled: true,
          revision: crypto.randomUUID(),
          updatedAt: Date.now(),
        })
        return candidate.handle
      })
    },
    async get(owner, projectId) {
      const key = identity(owner, projectId)
      return lock(async () => {
        const row = await storage.read(key)
        if (!row?.enabled || !(await granted(row.handle))) return null
        return row.handle
      })
    },
    async save(owner, projectId, handle) {
      const key = identity(owner, projectId)
      directory(handle)
      return lock(async () => {
        if (!(await granted(handle))) throw new Error('文件夹读写权限尚未批准，请重新连接原目录。')
        const previous = await storage.read(key)
        if (previous) {
          let same = false
          try {
            same = await directory(previous.handle).isSameEntry(handle)
          } catch {
            throw new Error('无法核对原目录，请添加新项目，不要将已有项目连接到其它目录。')
          }
          if (!same)
            throw new Error(
              '所选文件夹不是这个项目原先绑定的目录。请选择原目录；另一个目录请添加新项目。',
            )
        }
        await storage.put({
          ...key,
          handle,
          enabled: true,
          revision: crypto.randomUUID(),
          updatedAt: Date.now(),
        })
      })
    },
    async forget(owner, projectId) {
      const key = identity(owner, projectId)
      return lock(async () => {
        const row = await storage.read(key)
        // Keep identity for a future explicit reconnect, but never return this handle as authorized.
        if (row)
          await storage.put({
            ...row,
            enabled: false,
            revision: crypto.randomUUID(),
            updatedAt: Date.now(),
          })
      })
    },
    async remove(owner, projectId) {
      const key = identity(owner, projectId)
      return lock(() => storage.remove(key))
    },
    async forgetOwner(owner) {
      identity(owner, '00000000000000000000000000000000')
      return lock(async () => {
        for (const row of await storage.listOwner(owner)) {
          await storage.remove(identity(owner, row.conversationId))
        }
      })
    },
  }
}

const bindings = createCodingBindings(browserStorage, browserLock)
export const getCodingBinding = (owner, projectId) => bindings.get(owner, projectId)
export const prepareCodingReconnect = (owner, projectId) =>
  bindings.prepareReconnect(owner, projectId)
export const reconnectCodingBinding = (owner, projectId, candidate) =>
  bindings.reconnect(owner, projectId, candidate)
export const saveCodingBinding = (owner, projectId, root) => bindings.save(owner, projectId, root)
export const forgetCodingBinding = (owner, projectId) => bindings.forget(owner, projectId)
export const deleteCodingBinding = (owner, projectId) => bindings.remove(owner, projectId)
export const forgetOwnerCodingBindings = (owner) => bindings.forgetOwner(owner)
