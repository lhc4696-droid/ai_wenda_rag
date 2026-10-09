// Directory capabilities stay in this browser; the server only knows a conversation ID.
// Restoring a remembered handle checks permission, but never requests it or starts a task.
function identity(owner, conversationId) {
  if (!Number.isSafeInteger(owner) || owner <= 0) throw new Error('目录绑定缺少有效账号。')
  const id = String(conversationId).replaceAll('-', '').toLowerCase()
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('目录绑定缺少有效会话。')
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

export function createCodingBindings(storage, lock = (action) => action()) {
  return {
    async get(owner, conversationId) {
      const key = identity(owner, conversationId)
      return lock(async () => {
        const row = await storage.read(key)
        if (!row?.enabled || !(await granted(row.handle))) return null
        return row.handle
      })
    },
    async save(owner, conversationId, handle) {
      const key = identity(owner, conversationId)
      directory(handle)
      return lock(async () => {
        if (!(await granted(handle))) throw new Error('文件夹读写权限尚未批准，请重新连接原目录。')
        const previous = await storage.read(key)
        if (previous) {
          let same = false
          try {
            same = await directory(previous.handle).isSameEntry(handle)
          } catch {
            throw new Error('无法核对原目录，请新建编程对话，不要将旧对话直接连接到其它项目。')
          }
          if (!same)
            throw new Error(
              '所选文件夹不是这个对话原先绑定的目录。请选择原目录；另一个项目请新建对话。',
            )
        }
        await storage.put({ ...key, handle, enabled: true, updatedAt: Date.now() })
      })
    },
    async forget(owner, conversationId) {
      const key = identity(owner, conversationId)
      return lock(async () => {
        const row = await storage.read(key)
        // Keep identity for a future explicit reconnect, but never return this handle as authorized.
        if (row) await storage.put({ ...row, enabled: false, updatedAt: Date.now() })
      })
    },
    async remove(owner, conversationId) {
      const key = identity(owner, conversationId)
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
export const getCodingBinding = (owner, conversationId) => bindings.get(owner, conversationId)
export const saveCodingBinding = (owner, conversationId, root) =>
  bindings.save(owner, conversationId, root)
export const forgetCodingBinding = (owner, conversationId) => bindings.forget(owner, conversationId)
export const deleteCodingBinding = (owner, conversationId) => bindings.remove(owner, conversationId)
export const forgetOwnerCodingBindings = (owner) => bindings.forgetOwner(owner)
