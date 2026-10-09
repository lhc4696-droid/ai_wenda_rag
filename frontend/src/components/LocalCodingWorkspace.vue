<script setup>
import {
  computed,
  markRaw,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  shallowRef,
  toRaw,
  watch,
} from 'vue'
import AppIcon from './AppIcon.vue'
import ChatMessage from './ChatMessage.vue'
import { buildCodingFileTree } from '../lib/coding-view.js'
import { localCodingApi as api } from '../api/local-coding.js'
import {
  getCodingBinding,
  saveCodingBinding,
  forgetCodingBinding,
  deleteCodingBinding,
} from '../lib/coding-bindings.js'
import {
  LocalFolderSession,
  localSupport,
  listLocalJournals,
  restoreLocalJournal,
  deleteLocalJournal,
} from '../lib/local-files.js'
import '../coding.css'
import '../local-coding.css'

const props = defineProps({
  user: { type: Object, required: true },
  active: { type: Boolean, default: true },
})
const emit = defineEmits(['back', 'auth-expired'])
const supported = localSupport()
const instruction = ref('')
const task = ref(null)
const history = ref([])
const conversations = ref([])
const conversation = ref(null)
const turns = ref([])
const boundRoot = shallowRef(null)
const loadingConversation = ref(false)
const pendingQuestion = ref('')
const journals = ref([])
const files = ref([])
const changes = ref([])
const events = ref([])
const capabilities = ref(null)
const busy = ref(false)
const authorizing = ref(false)
const error = ref('')
const notice = ref('')
const directoryName = ref('尚未选择工作目录')
const thread = ref(null)
const textarea = ref(null)
const changesDialog = ref(null)
const fileFilter = ref('')
const collapsedFolders = ref(new Set())
const filesOpen = ref(window.innerWidth > 760)
const processOpen = ref(window.innerWidth > 1100)
const compact = ref(window.innerWidth <= 1100)
const mobile = ref(window.innerWidth <= 760)
const followBottom = ref(true)
const historyDetails = ref(null)
const consent = ref(null)
const consentMode = ref('start')
const recovery = ref(null)
const selectedPath = ref('')
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const labels = {
  queued: '等待模型',
  running: '模型处理中',
  waiting_tools: '本地文件操作',
  completed: '已结束',
  failed: '未完成',
  cancelled: '已停止',
  interrupted: '已中断',
}
const activeHistory = computed(() => history.value.filter((item) => !terminal.has(item.status)))
const legacyHistory = computed(() => history.value.filter((item) => !item.conversation_id))
const visibleChange = computed(
  () => changes.value.find((item) => item.path === selectedPath.value) || changes.value[0],
)
const locked = computed(() => busy.value || authorizing.value || loadingConversation.value)
const title = computed(
  () => conversation.value?.title || turns.value[0]?.instruction || '新的编程对话',
)
const fileRows = computed(() =>
  buildCodingFileTree(files.value, changes.value, fileFilter.value, collapsedFolders.value),
)
function answerFor(item) {
  if (item.summary) return item.summary
  if (item.error) return item.error
  if (terminal.has(item.status))
    return '本次执行已结束。你可以查看右侧过程和修改记录；已保存的文件不会自动撤销。'
  return ''
}
const currentProgress = computed(() => {
  if (loadingConversation.value) return '正在打开编程对话'
  if (authorizing.value) return '等待你选择并授权文件夹'
  if (busy.value && ['restore', 'clear'].includes(consentMode.value)) return '正在处理本地恢复记录'
  if (locked.value && !task.value) return '正在读取目录结构'
  return task.value ? labels[task.value.status] : '尚未开始执行'
})
const sendHint = computed(() => {
  if (!supported) return '当前浏览器不支持目录读写，请查看上方提示。'
  if (locked.value) return '本次操作进行中，可以随时停止。'
  if (activeHistory.value.length) return '有未结束的旧任务，请先在左侧历史记录中停止。'
  if (!instruction.value.trim())
    return boundRoot.value
      ? '继续说出需求，将在同一项目中接着完成。'
      : '输入需求，首次发送时连接项目目录。'
  return boundRoot.value
    ? '使用当前项目目录继续执行，无需重复选择。'
    : '连接此对话的项目目录后继续。'
})
const panelOverlay = computed(
  () => (compact.value && processOpen.value) || (mobile.value && filesOpen.value),
)

function togglePanel(panel) {
  if (panel === 'files') {
    filesOpen.value = !filesOpen.value
    if (mobile.value && filesOpen.value) processOpen.value = false
    if (!filesOpen.value) nextTick(() => document.getElementById('coding-toggle-files')?.focus())
  } else {
    processOpen.value = !processOpen.value
    if (mobile.value && processOpen.value) filesOpen.value = false
    if (!processOpen.value)
      nextTick(() => document.getElementById('coding-toggle-process')?.focus())
  }
}
function closePanels(restoreFocus = false) {
  const panel = compact.value && processOpen.value ? 'process' : 'files'
  if (compact.value) processOpen.value = false
  if (mobile.value) filesOpen.value = false
  if (restoreFocus === true)
    nextTick(() => document.getElementById(`coding-toggle-${panel}`)?.focus())
}
function dismissSidePanel(event) {
  if (!panelOverlay.value || consent.value?.open || changesDialog.value?.open) return
  event.preventDefault()
  closePanels(true)
}
function fitPanels() {
  const nextCompact = window.innerWidth <= 1100
  const nextMobile = window.innerWidth <= 760
  if (nextCompact && !compact.value) processOpen.value = false
  if (nextMobile && !mobile.value) filesOpen.value = false
  compact.value = nextCompact
  mobile.value = nextMobile
}
function toggleFolder(path) {
  const next = new Set(collapsedFolders.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsedFolders.value = next
}
function resizeInput() {
  const field = textarea.value
  if (!field) return
  field.style.height = 'auto'
  field.style.height = `${Math.min(field.scrollHeight, 168)}px`
}
function inputKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  askStart()
}
function trackScroll() {
  const element = thread.value
  if (element)
    followBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100
}
async function scrollChat(force = false) {
  await nextTick()
  if (thread.value && (force || followBottom.value))
    thread.value.scrollTop = thread.value.scrollHeight
}
function fillSuggestion(value) {
  if (locked.value) return
  instruction.value = value
  nextTick(() => textarea.value?.focus())
}
async function openChanges(path = '') {
  if (!changes.value.length) return
  selectedPath.value = changes.value.some((entry) => entry.path === path)
    ? path
    : changes.value[0].path
  await nextTick()
  if (!changesDialog.value?.open) changesDialog.value?.showModal()
}
function openHistoryList() {
  filesOpen.value = true
  if (mobile.value) processOpen.value = false
  nextTick(() => {
    if (historyDetails.value) historyDetails.value.open = true
  })
}
function newTaskView() {
  if (locked.value) return
  ++viewGeneration
  conversation.value = null
  turns.value = []
  boundRoot.value = null
  task.value = null
  pendingQuestion.value = ''
  instruction.value = ''
  rememberConversation(null)
  directoryName.value = '尚未选择工作目录'
  files.value = []
  changes.value = []
  events.value = []
  selectedPath.value = ''
  fileFilter.value = ''
  collapsedFolders.value = new Set()
  notice.value = ''
  error.value = ''
  closePanels()
  nextTick(() => textarea.value?.focus())
}
let alive = true
let generation = 0
let viewGeneration = 0
let refreshGeneration = 0
let session = null
let requestController = null
let restoreController = null
let pendingInstruction = ''
const taskEvents = new Map()
const componentOwner = props.user.id

function rememberConversation(id) {
  try {
    const key = `zhiyan-coding-conversation:${props.user.id}`
    if (id) sessionStorage.setItem(key, id)
    else sessionStorage.removeItem(key)
  } catch {
    // Browser storage may be disabled; the open conversation still works in memory.
  }
}
function upsertTurn(value) {
  task.value = value
  const index = turns.value.findIndex((item) => item.id === value.id)
  if (index < 0) turns.value.push(value)
  else turns.value[index] = value
}
function changesFor(id) {
  return journals.value.find((item) => item.taskId === id)?.entries || []
}
function selectTurn(item) {
  if (locked.value) return
  task.value = item
  changes.value = changesFor(item.id)
  events.value = taskEvents.get(item.id) || []
  if (!boundRoot.value) files.value = changes.value.map((entry) => ({ path: entry.path }))
  processOpen.value = true
  if (mobile.value) filesOpen.value = false
}
async function showTurnChanges(item) {
  selectTurn(item)
  await openChanges()
}
async function disconnectDirectory() {
  if (locked.value || !conversation.value) return
  const id = conversation.value.id
  const owner = props.user.id
  boundRoot.value = null
  authorizing.value = true
  try {
    await forgetCodingBinding(owner, id)
    if (alive)
      notice.value = '已断开此对话的目录访问。聊天和本地文件保留，继续操作时需要重新连接原目录。'
  } catch (cause) {
    failure(cause)
  } finally {
    authorizing.value = false
  }
}

function failure(cause) {
  if (!alive || cause.name === 'AbortError') return
  if (cause.status === 401) {
    emit('auth-expired')
    return
  }
  error.value = cause.message || '操作未完成。'
}
function event(message, kind = 'status') {
  events.value.push({ message, kind })
}
function formatTime(value) {
  return new Date(value * 1000).toLocaleString('zh-CN')
}
function requestSignal() {
  return AbortSignal.any([requestController.signal, AbortSignal.timeout(20000)])
}
function pause(ms) {
  const signal = requestController.signal
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Stopped', 'AbortError'))
      return
    }
    const finish = () => {
      signal.removeEventListener('abort', abort)
      resolve()
    }
    const timer = setTimeout(finish, ms)
    const abort = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      reject(new DOMException('Stopped', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}
async function refresh() {
  if (!alive) return
  const owner = props.user.id
  const request = ++refreshGeneration
  const results = await Promise.allSettled([
    api.list(),
    listLocalJournals(owner),
    api.capabilities(),
    api.listConversations(),
  ])
  if (!alive || props.user.id !== owner || request !== refreshGeneration) return
  if (results[0].status === 'fulfilled') history.value = results[0].value.items
  else failure(results[0].reason)
  if (results[1].status === 'fulfilled') {
    journals.value = results[1].value.filter((item) => item.entries.length)
    if (!session && task.value) {
      const journal = journals.value.find((item) => item.taskId === task.value.id)
      changes.value = journal?.entries || []
    }
  } else failure(results[1].reason)
  if (results[2].status === 'fulfilled') capabilities.value = results[2].value
  else failure(results[2].reason)
  if (results[3].status === 'fulfilled') {
    conversations.value = results[3].value.items
    const current = conversations.value.find((item) => item.id === conversation.value?.id)
    if (current) conversation.value = current
  } else failure(results[3].reason)
}

async function askStart() {
  if (locked.value || !instruction.value.trim() || !supported || activeHistory.value.length) return
  pendingInstruction = instruction.value.trim()
  consentMode.value = 'start'
  recovery.value = null
  error.value = ''
  notice.value = ''
  const run = ++generation
  const owner = props.user.id
  const root = boundRoot.value
  if (root && conversation.value) {
    authorizing.value = true
    try {
      // Recheck both the app's binding and browser permission, without prompting.
      const currentRoot = await getCodingBinding(owner, conversation.value.id)
      if (!alive || !props.active || run !== generation || props.user.id !== owner) return
      if (currentRoot) {
        boundRoot.value = markRaw(currentRoot)
        authorizing.value = false
        await executeInDirectory(currentRoot, run)
        return
      }
      boundRoot.value = null
      notice.value = '目录连接已断开或浏览器权限已失效，请重新连接此项目的原目录。'
    } catch (cause) {
      failure(cause)
      return
    } finally {
      authorizing.value = false
    }
  }
  if (!alive || !props.active || run !== generation || props.user.id !== owner) return
  await nextTick()
  if (!alive || !props.active || run !== generation || props.user.id !== owner) return
  if (!consent.value?.open) consent.value?.showModal()
}
async function askConnect() {
  if (locked.value || !supported) return
  const run = ++generation
  pendingInstruction = ''
  consentMode.value = 'connect'
  recovery.value = null
  error.value = ''
  await nextTick()
  if (!alive || !props.active || run !== generation) return
  if (!consent.value?.open) consent.value?.showModal()
}
async function askRecovery(journal, mode) {
  if (locked.value) return
  const run = ++generation
  recovery.value = structuredClone(toRaw(journal))
  consentMode.value = mode
  await nextTick()
  if (!alive || !props.active || run !== generation) return
  consent.value.showModal()
}

async function permitOnce() {
  if (locked.value) return
  const run = ++generation
  const mode = consentMode.value
  const owner = props.user.id
  const view = viewGeneration
  let executionOwnsCleanup = false
  const current = () =>
    alive &&
    props.active &&
    run === generation &&
    view === viewGeneration &&
    props.user.id === owner
  const assertCurrent = () => {
    if (!current()) throw new DOMException('Stopped', 'AbortError')
  }
  if (mode === 'clear') {
    busy.value = true
    consent.value.close()
    try {
      await deleteLocalJournal(recovery.value.key)
      notice.value = '已清除这条浏览器恢复记录，磁盘文件未被删除。'
    } catch (cause) {
      failure(cause)
    } finally {
      if (alive) await refresh()
      busy.value = false
    }
    return
  }
  authorizing.value = true
  error.value = ''
  notice.value = ''
  try {
    // Must be the direct result of this trusted click, before network/IDB awaits.
    const root = await window.showDirectoryPicker({
      mode: 'readwrite',
      id: 'zhiyan-coding-project',
    })
    assertCurrent()
    consent.value.close()
    if (mode === 'start' || mode === 'connect') {
      let currentConversation = conversation.value
      if (!currentConversation) {
        currentConversation = await api.createConversation({
          client_conversation_id: crypto.randomUUID(),
          directory_name: root.name.slice(0, 120),
        })
        assertCurrent()
        conversation.value = currentConversation
        turns.value = []
        task.value = null
        rememberConversation(currentConversation.id)
      }
      if (root.name.slice(0, 120) !== currentConversation.directory_name)
        throw new Error(
          `请选择此项目原先的目录“${currentConversation.directory_name}”。不同项目请新建对话。`,
        )
      // The binding helper checks handle identity, never just a matching folder name.
      await saveCodingBinding(owner, currentConversation.id, root)
      if (!current()) {
        await forgetCodingBinding(owner, currentConversation.id)
        return
      }
      boundRoot.value = markRaw(root)
      directoryName.value = root.name
      if (mode === 'connect') {
        notice.value = '项目目录已连接。在此对话中继续发送需求，无需每轮重新选择或授权。'
        return
      }
      executionOwnsCleanup = true
      authorizing.value = false
      await executeInDirectory(root, run)
      return
    }
    await navigator.locks.request('zhiyan-local-file-task', { ifAvailable: true }, async (lock) => {
      if (!lock) throw new Error('另一个知言标签页正在操作本地文件，请等待其结束。')
      assertCurrent()
      authorizing.value = false
      busy.value = true
      if (mode === 'restore') {
        restoreController = new AbortController()
        const result = await restoreLocalJournal(
          root,
          structuredClone(toRaw(recovery.value)),
          restoreController.signal,
        )
        notice.value =
          `已恢复或确认未变更 ${result.restored.length} 个文件。` +
          (result.conflicts.length
            ? `以下文件有冲突，未覆盖：${result.conflicts.join('、')}`
            : '新建的空目录会保留。')
      }
    })
  } catch (cause) {
    if (cause.name === 'AbortError' && alive)
      notice.value = '目录选择或本次操作已取消，未执行新的文件操作。'
    else failure(cause)
  } finally {
    if (!executionOwnsCleanup) {
      session?.revoke()
      session = null
      restoreController = null
      requestController?.abort()
      consent.value?.close()
      if (alive) await refresh()
      busy.value = false
      authorizing.value = false
    }
  }
}

async function executeInDirectory(root, run) {
  busy.value = true
  try {
    await navigator.locks.request('zhiyan-local-file-task', { ifAvailable: true }, async (lock) => {
      if (!lock) throw new Error('另一个知言标签页正在操作本地文件，请等待其结束。')
      if (!alive || !props.active || run !== generation) return
      await runTask(root, run)
    })
  } catch (cause) {
    failure(cause)
  } finally {
    if (task.value) taskEvents.set(task.value.id, [...events.value])
    session?.revoke()
    session = null
    requestController?.abort()
    requestController = null
    pendingQuestion.value = ''
    if (alive) await refresh()
    busy.value = false
  }
}

async function runTask(root, run) {
  const owner = props.user.id
  const conversationId = conversation.value?.id
  if (!conversationId || boundRoot.value !== root) throw new Error('请先连接此对话的项目目录。')
  const clientId = crypto.randomUUID()
  const grantToken = crypto.randomUUID() + crypto.randomUUID()
  const controller = new AbortController()
  requestController = controller
  const current = () =>
    alive &&
    props.active &&
    run === generation &&
    !controller.signal.aborted &&
    props.user.id === owner &&
    conversation.value?.id === conversationId &&
    boundRoot.value === root
  const assertCurrent = () => {
    if (!current()) throw new DOMException('Stopped', 'AbortError')
  }
  events.value = []
  files.value = []
  changes.value = []
  task.value = null
  pendingQuestion.value = pendingInstruction
  selectedPath.value = ''
  fileFilter.value = ''
  collapsedFolders.value = new Set()
  followBottom.value = true
  directoryName.value = root.name
  const saved = await listLocalJournals(owner)
  assertCurrent()
  if (saved.filter((j) => j.entries.length).length >= 10)
    throw new Error('已保留 10 份本地恢复记录，请先处理并清除不再需要的记录。')
  session = new LocalFolderSession(root, owner, clientId, () => {
    if (!current() || !session) return
    files.value = [...session.files.values()].sort((a, b) => a.path.localeCompare(b.path))
    changes.value = session.journal.entries.map((entry) => ({ ...entry }))
  })
  const local = session
  event('使用当前对话已连接的目录，重新查看最新目录结构；暂不读取全部文件正文。')
  const scan = await local.scan()
  assertCurrent()
  event(
    `找到 ${scan.paths.length} 个可访问源码文件；跳过 ${scan.ignored} 个敏感目录、不支持或过大的文件。`,
  )
  let identifier = null
  try {
    const submittedAt = performance.now()
    const accepted = await api.create(
      {
        instruction: pendingInstruction,
        conversation_id: conversationId,
        directory_name: root.name.slice(0, 120),
        client_task_id: clientId,
        grant_token: grantToken,
        paths: scan.paths,
      },
      requestSignal(),
    )
    identifier = accepted.id
    local.journal.taskId = identifier
    assertCurrent()
    if (
      accepted.conversation_id !== conversationId ||
      accepted.client_task_id !== clientId.replaceAll('-', '')
    )
      throw new Error('服务端返回的任务与当前项目对话不匹配，已停止。')
    upsertTurn(accepted)
    pendingQuestion.value = ''
    instruction.value = ''
    const expires = submittedAt + Math.max(0, accepted.deadline_at - accepted.created_at) * 1000
    local.expiresAt = expires
    while (current()) {
      if (performance.now() >= expires)
        throw new Error('本轮执行达到时间上限，已停止文件操作。目录仍绑定此对话，可继续发送需求。')
      const state = await api.get(identifier, requestSignal())
      assertCurrent()
      if (state.client_task_id !== clientId.replaceAll('-', ''))
        throw new Error('任务与当前授权不匹配，已停止。')
      if (state.conversation_id !== conversationId)
        throw new Error('任务与当前编程对话不匹配，已停止。')
      upsertTurn(state)
      if (terminal.has(state.status)) break
      if (state.status !== 'waiting_tools') {
        await pause(1000)
        continue
      }
      const results = []
      for (const call of state.pending_tools) {
        assertCurrent()
        // Recheck login and server cancellation before each browser tool.
        const latest = await api.get(identifier, requestSignal())
        assertCurrent()
        if (latest.status !== 'waiting_tools' || latest.round !== state.round)
          throw new Error('任务状态已经变化，停止本地操作。')
        // A different tab may have disconnected this conversation or cleared login bindings.
        const activeRoot = await getCodingBinding(owner, conversationId)
        assertCurrent()
        if (!activeRoot || !(await root.isSameEntry(activeRoot))) {
          boundRoot.value = null
          throw new Error('项目目录绑定已断开或权限失效，本轮已停止。重新连接原目录后才能继续。')
        }
        assertCurrent()
        event(`调用 ${call.function.name}`, 'tool')
        const result = await local.execute(call, state.round)
        event(
          result.ok ? `操作完成${result.path ? '：' + result.path : ''}` : result.error,
          result.ok ? 'tool' : 'error',
        )
        if (result.fatal) throw new Error(result.error + ' 请检查本地恢复记录，当前任务已停止。')
        results.push({ tool_call_id: call.id, result: JSON.stringify(result) })
      }
      assertCurrent()
      // Results are acknowledged once per round; no tool is rerun on network failure.
      const updated = await api.results(
        identifier,
        { grant_token: grantToken, round: state.round, results },
        requestSignal(),
      )
      assertCurrent()
      upsertTurn(updated)
    }
    if (current())
      notice.value =
        '本轮执行已结束，改动已保留在本地。你可以在此对话继续提出需求，无需重新选择目录。'
  } finally {
    local.revoke()
    if (identifier && (!task.value || !terminal.has(task.value.status))) {
      const stopped = await api.cancel(identifier).catch(() => null)
      if (
        alive &&
        stopped &&
        conversation.value?.id === conversationId &&
        task.value?.id === identifier
      )
        upsertTurn(stopped)
    }
  }
}

function stop() {
  ++generation
  if (loadingConversation.value) {
    ++viewGeneration
    loadingConversation.value = false
  }
  session?.revoke() // Synchronously close the app permission gate, before network.
  requestController?.abort()
  restoreController?.abort()
  consent.value?.close()
  changesDialog.value?.close()
  // Merely viewing an unfinished history item must not cancel another tab's execution.
  if (session && task.value && !terminal.has(task.value.status)) api.stopOnLeave(task.value.id)
  if (busy.value || authorizing.value)
    notice.value =
      '已停止本轮文件操作，正在收尾。项目目录仍与此对话绑定，已保存的改动不会自动撤销。'
}
async function cancelHistory(item) {
  try {
    const stopped = await api.cancel(item.id)
    if (task.value?.id === item.id) upsertTurn(stopped)
    await refresh()
  } catch (cause) {
    failure(cause)
  }
}
async function removeHistory(item) {
  try {
    await api.remove(item.id)
    if (task.value?.id === item.id) newTaskView()
    await refresh()
    notice.value = '已删除服务端任务记录，本地文件和浏览器恢复记录未被删除。'
  } catch (cause) {
    failure(cause)
  }
}
function showHistory(item) {
  if (locked.value) return
  ++viewGeneration
  conversation.value = null
  boundRoot.value = null
  turns.value = [item]
  task.value = item
  pendingQuestion.value = ''
  instruction.value = ''
  rememberConversation(null)
  directoryName.value = item.directory_name
  events.value = []
  const journal = journals.value.find((j) => j.taskId === item.id)
  changes.value = journal?.entries || []
  // Historical paths are recovery-record metadata, not a fresh filesystem scan.
  files.value = changes.value.map((entry) => ({ path: entry.path }))
  selectedPath.value = ''
  fileFilter.value = ''
  collapsedFolders.value = new Set()
  followBottom.value = true
  closePanels()
  scrollChat(true)
  notice.value =
    '这是旧版的独立任务记录，仅供查看。发送新需求会创建新的项目对话，不会把旧任务当作上下文。'
}
async function showConversation(item) {
  if (locked.value) return
  const owner = props.user.id
  const view = ++viewGeneration
  const current = () => alive && props.user.id === owner && view === viewGeneration
  loadingConversation.value = true
  boundRoot.value = null
  error.value = ''
  notice.value = ''
  instruction.value = ''
  changesDialog.value?.close()
  try {
    const detail = await api.getConversation(item.id)
    if (!current()) return
    conversation.value = detail
    turns.value = detail.tasks || []
    task.value = turns.value.at(-1) || null
    events.value = taskEvents.get(task.value?.id) || []
    changes.value = task.value ? changesFor(task.value.id) : []
    files.value = changes.value.map((entry) => ({ path: entry.path }))
    fileFilter.value = ''
    collapsedFolders.value = new Set()
    directoryName.value = detail.directory_name
    rememberConversation(detail.id)
    const root = supported ? await getCodingBinding(owner, detail.id) : null
    if (!current()) return
    boundRoot.value = root ? markRaw(root) : null
    notice.value = root
      ? '已打开项目对话，目录连接仍有效。下一轮会重新读取最新文件，不会自动执行旧任务。'
      : '已打开历史对话。继续编程前请连接此项目的原目录；不会自动执行旧任务。'
    closePanels()
    followBottom.value = true
    scrollChat(true)
  } catch (cause) {
    if (current()) failure(cause)
  } finally {
    if (current()) loadingConversation.value = false
  }
}
async function removeConversation(item) {
  if (locked.value) return
  if (!window.confirm('删除这段编程对话及其服务端执行记录？本地项目文件和浏览器恢复记录不会删除。'))
    return
  const owner = props.user.id
  const view = ++viewGeneration
  const current = () => alive && props.user.id === owner && view === viewGeneration
  loadingConversation.value = true
  try {
    await api.removeConversation(item.id)
    await deleteCodingBinding(owner, item.id)
    if (!current()) return
    await refresh()
    if (!current()) return
    loadingConversation.value = false
    if (conversation.value?.id === item.id) newTaskView()
    notice.value = '已删除编程对话并移除此对话的目录绑定；本地文件和恢复记录保留。'
  } catch (cause) {
    if (current()) failure(cause)
  } finally {
    if (current()) loadingConversation.value = false
  }
}
function beforeLeave(event) {
  if (locked.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
watch(
  () => props.active,
  (active) => {
    if (!active) stop()
    else refresh()
  },
)
watch(instruction, () => nextTick(resizeInput))
watch([turns, pendingQuestion, locked], () => scrollChat(), { deep: true })
onMounted(async () => {
  const initialGeneration = generation
  const initialView = viewGeneration
  resizeInput()
  window.addEventListener('resize', fitPanels)
  window.addEventListener('pagehide', stop)
  window.addEventListener('beforeunload', beforeLeave)
  await refresh()
  if (!alive || props.user.id !== componentOwner) return
  try {
    const id = sessionStorage.getItem(`zhiyan-coding-conversation:${componentOwner}`)
    const item = conversations.value.find((entry) => entry.id === id)
    if (
      item &&
      props.active &&
      !conversation.value &&
      !locked.value &&
      !consent.value?.open &&
      !instruction.value.trim() &&
      generation === initialGeneration &&
      viewGeneration === initialView
    )
      await showConversation(item)
  } catch {
    // Failure to remember the selected conversation does not block manual selection.
  }
})
onUnmounted(() => {
  alive = false
  ++viewGeneration
  stop()
  boundRoot.value = null
  window.removeEventListener('pagehide', stop)
  window.removeEventListener('beforeunload', beforeLeave)
  window.removeEventListener('resize', fitPanels)
})
</script>

<template>
  <div class="coding-chat-shell" @keydown.esc="dismissSidePanel">
    <header class="cc-header">
      <div class="cc-header-leading">
        <button
          class="cc-button"
          type="button"
          :disabled="locked"
          aria-label="返回基础聊天"
          title="返回基础聊天"
          @click="emit('back')"
        >
          <AppIcon name="chat" :size="16" />
          <span>返回聊天</span>
        </button>
        <span class="model-label">
          <AppIcon name="spark" :size="15" />
          <span class="model-name" :title="capabilities?.model">
            {{ capabilities?.model || '编程模型' }}
          </span>
        </span>
      </div>
      <div class="cc-header-title">
        <h1 :title="title">{{ title }}</h1>
        <p>{{ conversation || turns.length ? directoryName : '知言 AI · 在你的项目里工作' }}</p>
      </div>
      <div class="cc-header-actions">
        <button
          id="coding-toggle-files"
          class="cc-panel-toggle"
          type="button"
          :aria-pressed="filesOpen"
          :aria-expanded="filesOpen"
          aria-controls="coding-files-panel"
          :title="filesOpen ? '收起项目目录' : '打开项目目录'"
          @click="togglePanel('files')"
        >
          <AppIcon name="folder" :size="17" />
          <span>目录</span>
        </button>
        <button
          id="coding-toggle-process"
          class="cc-panel-toggle"
          type="button"
          :aria-pressed="processOpen"
          :aria-expanded="processOpen"
          aria-controls="coding-process-panel"
          :title="processOpen ? '收起执行过程' : '打开执行过程'"
          @click="togglePanel('process')"
        >
          <AppIcon name="activity" :size="17" />
          <span>过程</span>
        </button>
        <div class="cc-account" :title="user.username">
          <span class="cc-avatar" aria-hidden="true">
            {{ user.username?.slice(0, 1).toUpperCase() }}
          </span>
          <span>{{ user.username }}</span>
        </div>
      </div>
    </header>

    <div
      class="cc-layout"
      :class="{ 'is-files-hidden': !filesOpen, 'is-process-hidden': !processOpen }"
    >
      <button
        v-if="panelOverlay"
        class="cc-panel-backdrop"
        type="button"
        aria-label="收起侧边面板"
        @click="closePanels(true)"
      />

      <aside
        v-show="filesOpen"
        id="coding-files-panel"
        class="cc-panel cc-files-panel"
        :inert="compact && processOpen"
        aria-label="项目目录"
      >
        <div class="cc-panel-header">
          <h2>
            <AppIcon name="folder" :size="16" />
            项目目录
            <small>{{ files.length }}</small>
          </h2>
          <button
            class="cc-icon-button"
            type="button"
            aria-label="收起项目目录"
            @click="togglePanel('files')"
          >
            <AppIcon name="close" :size="16" />
          </button>
        </div>
        <div class="cc-panel-scroll">
          <div class="cc-folder-chip" :title="directoryName">
            <AppIcon name="folder" :size="17" />
            <span>{{ directoryName }}</span>
          </div>
          <p class="cc-permission-hint">
            <AppIcon name="lock" :size="12" />
            {{ boundRoot ? '已连接 · 后续消息复用此目录' : '未连接 · 仅查看已有聊天和修改记录' }}
          </p>
          <div class="cc-binding-actions">
            <button
              v-if="!boundRoot"
              class="cc-button"
              type="button"
              :disabled="locked || !supported"
              @click="askConnect"
            >
              <AppIcon name="folder" :size="14" />
              {{ conversation ? '重新连接原目录' : '连接项目目录' }}
            </button>
            <button
              v-else
              class="cc-button"
              type="button"
              :disabled="locked"
              @click="disconnectDirectory"
            >
              <AppIcon name="lock" :size="13" />
              断开目录连接
            </button>
          </div>
          <label for="coding-file-filter" class="sr-only">筛选项目文件</label>
          <input
            id="coding-file-filter"
            v-model="fileFilter"
            class="cc-file-filter"
            type="search"
            placeholder="筛选文件…"
            :disabled="!files.length"
          />
          <div class="cc-file-list" aria-label="可访问文件列表">
            <p v-if="!fileRows.length" class="cc-panel-empty">
              {{
                fileFilter
                  ? '没有匹配的文件。'
                  : task
                    ? '没有可展示的路径。历史仅展示本浏览器保存的改动文件，不会重新读取目录。'
                    : '发送需求后会刷新项目目录。也可以连接空目录开始。'
              }}
            </p>
            <template v-for="row in fileRows" :key="row.path">
              <button
                v-if="row.kind === 'directory'"
                class="cc-file-row is-folder"
                type="button"
                :style="{ paddingLeft: 8 + row.depth * 14 + 'px' }"
                :title="row.path"
                :aria-expanded="row.expanded"
                @click="toggleFolder(row.path)"
              >
                <AppIcon :name="row.expanded ? 'chevron-down' : 'chevron'" :size="12" />
                <AppIcon name="folder" :size="15" />
                <span>{{ row.name }}</span>
              </button>
              <div
                v-else
                class="cc-file-row"
                :class="{ 'is-changed': row.changed }"
                :style="{ paddingLeft: 24 + row.depth * 14 + 'px' }"
                :title="row.path"
              >
                <AppIcon name="file" :size="14" />
                <span>{{ row.name }}</span>
                <button
                  v-if="row.changed"
                  class="cc-icon-button"
                  type="button"
                  :aria-label="'查看修改：' + row.path"
                  title="查看修改对照"
                  @click="openChanges(row.path)"
                >
                  <AppIcon name="pen" :size="12" />
                </button>
              </div>
            </template>
          </div>
          <button class="cc-button" type="button" :disabled="locked" @click="newTaskView">
            <AppIcon name="plus" :size="15" />
            新建项目对话
          </button>
          <details ref="historyDetails" class="cc-disclosure">
            <summary>
              <AppIcon name="chat" :size="14" />
              项目对话
              <small>{{ conversations.length }}</small>
            </summary>
            <button class="cc-button" type="button" :disabled="locked" @click="refresh">
              <AppIcon name="refresh" :size="13" />
              刷新记录
            </button>
            <p v-if="!conversations.length" class="cc-panel-empty">
              连接项目后的对话会保存在这里。
            </p>
            <nav class="cc-history-list" aria-label="项目编程对话历史">
              <div
                v-for="item in conversations"
                :key="item.id"
                class="cc-history-item"
                :class="{ 'is-selected': conversation?.id === item.id }"
              >
                <button type="button" :disabled="locked" @click="showConversation(item)">
                  <strong>{{ item.title || '新的编程对话' }}</strong>
                  <small>{{ item.directory_name }} · {{ item.turn_count }} 轮对话</small>
                  <small>{{ formatTime(item.updated_at) }}</small>
                </button>
                <div class="cc-history-actions">
                  <button type="button" :disabled="locked" @click="removeConversation(item)">
                    删除对话
                  </button>
                </div>
              </div>
            </nav>
            <div v-if="activeHistory.length" class="cc-stale-tasks">
              <p class="cc-permission-hint">这些执行尚未结束，停止后才能发送新的需求：</p>
              <div v-for="item in activeHistory" :key="item.id" class="cc-history-item">
                <strong>{{ item.instruction }}</strong>
                <div class="cc-history-actions">
                  <button type="button" :disabled="locked" @click="cancelHistory(item)">
                    停止旧执行
                  </button>
                </div>
              </div>
            </div>
            <details v-if="legacyHistory.length" class="cc-disclosure">
              <summary>旧版独立任务</summary>
              <div v-for="item in legacyHistory" :key="item.id" class="cc-history-item">
                <button type="button" :disabled="locked" @click="showHistory(item)">
                  <strong>{{ item.instruction }}</strong>
                </button>
                <div class="cc-history-actions">
                  <button
                    v-if="terminal.has(item.status)"
                    type="button"
                    :disabled="locked"
                    @click="removeHistory(item)"
                  >
                    删除旧记录
                  </button>
                </div>
              </div>
            </details>
          </details>
        </div>
      </aside>

      <main class="cc-chat-column" :inert="panelOverlay" aria-label="编程对话">
        <section
          ref="thread"
          class="cc-thread"
          aria-label="项目编程对话的完整消息记录"
          @scroll.passive="trackScroll"
        >
          <div class="cc-messages">
            <div v-if="!supported" class="cc-notice is-error" role="alert">
              当前浏览器或地址不支持目录读写。请使用支持此功能的桌面 Chrome / Edge，通过 HTTPS
              或本机 localhost 打开。
            </div>
            <div v-if="error" class="cc-notice is-error" role="alert">
              <span>{{ error }}</span>
              <button type="button" aria-label="关闭错误提示" @click="error = ''">
                <AppIcon name="close" :size="14" />
              </button>
            </div>
            <div v-if="notice" class="cc-notice" role="status">
              <span>{{ notice }}</span>
              <button type="button" aria-label="关闭状态提示" @click="notice = ''">
                <AppIcon name="close" :size="14" />
              </button>
            </div>
            <div
              v-if="!turns.length && !pendingQuestion && !loadingConversation"
              class="cc-welcome"
            >
              <span class="cc-welcome-mark"><AppIcon name="spark" :size="28" /></span>
              <h2>把想法，写进你的项目</h2>
              <p>连接一次项目目录，在同一个对话中持续创建、修改和完善代码。</p>
              <div class="cc-suggestions">
                <button
                  type="button"
                  :disabled="locked"
                  @click="
                    fillSuggestion(
                      '阅读我选择的项目，解释目录结构和主要模块的职责，暂时不要修改文件。',
                    )
                  "
                >
                  <AppIcon name="book" :size="18" />
                  <strong>读懂项目</strong>
                  <span>先梳理结构，再开始动手</span>
                </button>
                <button
                  type="button"
                  :disabled="locked"
                  @click="
                    fillSuggestion(
                      '在我选择的空目录中，创建一个简洁的 HTML、CSS 和 JavaScript 登录页面，不安装依赖、不执行命令。',
                    )
                  "
                >
                  <AppIcon name="pen" :size="18" />
                  <strong>实现一个想法</strong>
                  <span>从空目录开始编写代码</span>
                </button>
              </div>
            </div>
            <div v-if="loadingConversation" class="cc-inline-status" role="status">
              正在打开项目对话…
            </div>
            <section
              v-for="(item, index) in turns"
              :key="item.id"
              class="cc-conversation-turn"
              :aria-label="'第 ' + (index + 1) + ' 轮对话'"
            >
              <ChatMessage :message="{ role: 'user', content: item.instruction }" />
              <ChatMessage
                v-if="answerFor(item)"
                :message="{ role: 'assistant', content: answerFor(item) }"
              />
              <div v-else-if="!terminal.has(item.status)" class="cc-inline-status" role="status">
                <span class="mini-brand"><AppIcon name="spark" :size="17" /></span>
                <span>
                  {{ item.id === task?.id && busy ? currentProgress : labels[item.status] }}
                  <small>详细步骤显示在右侧「执行过程」</small>
                </span>
                <span
                  class="cc-dot"
                  :class="{ 'is-running': busy && item.id === task?.id }"
                  aria-hidden="true"
                ></span>
              </div>
              <p v-else class="cc-inline-status">本次操作未继续；请查看上方提示或右侧执行记录。</p>
              <div class="cc-turn-actions">
                <button
                  class="cc-button"
                  type="button"
                  :disabled="locked"
                  :aria-pressed="item.id === task?.id"
                  @click="selectTurn(item)"
                >
                  <AppIcon name="activity" :size="13" />
                  {{ item.id === task?.id ? '正在查看此轮过程' : '查看此轮过程' }}
                </button>
                <button
                  v-if="changesFor(item.id).length"
                  class="cc-button"
                  type="button"
                  :disabled="locked"
                  @click="showTurnChanges(item)"
                >
                  <AppIcon name="layers" :size="13" />
                  {{ changesFor(item.id).length }} 个修改记录
                </button>
              </div>
            </section>
            <template v-if="pendingQuestion">
              <ChatMessage :message="{ role: 'user', content: pendingQuestion }" />
              <div class="cc-inline-status" role="status">
                <span class="cc-dot is-running"></span>
                正在读取当前项目的最新目录…
              </div>
            </template>
          </div>
        </section>

        <div class="cc-composer-dock">
          <form class="cc-composer" @submit.prevent="askStart">
            <div class="cc-composer-meta">
              <span>
                <AppIcon name="folder" :size="14" />
                本地文件编程
              </span>
              <span>
                {{
                  locked
                    ? '本轮操作进行中'
                    : boundRoot
                      ? '项目已连接 · 持续对话'
                      : '连接项目后持续对话'
                }}
              </span>
            </div>
            <label for="local-instruction" class="sr-only">输入编程需求</label>
            <textarea
              id="local-instruction"
              ref="textarea"
              v-model="instruction"
              :disabled="locked"
              maxlength="6000"
              rows="2"
              :placeholder="
                turns.length ? '继续说出你想修改或完善的地方…' : '想在项目中完成什么？说说你的需求…'
              "
              aria-describedby="coding-send-hint"
              @keydown="inputKeydown"
            />
            <div class="cc-composer-footer">
              <span id="coding-send-hint" class="cc-composer-hint">{{ sendHint }}</span>
              <button
                v-if="locked"
                class="cc-send-button is-stop"
                type="button"
                :disabled="loadingConversation"
                title="停止本轮操作，保留项目连接"
                aria-label="停止本轮操作，保留项目连接"
                @click="stop"
              >
                <AppIcon name="stop" :size="17" />
              </button>
              <button
                v-else
                class="cc-send-button"
                type="submit"
                :disabled="!supported || !instruction.trim() || Boolean(activeHistory.length)"
                :title="sendHint"
                :aria-label="boundRoot ? '发送并继续当前项目' : '发送并连接项目目录'"
              >
                <AppIcon name="arrow" :size="19" />
              </button>
            </div>
            <button
              v-if="activeHistory.length && !locked"
              class="cc-button"
              type="button"
              @click="openHistoryList"
            >
              查看未结束的任务
            </button>
          </form>
          <div class="cc-composer-disclaimer">
            <span>必要代码片段会发送给 AI；仅在你发送需求时执行，可随时断开目录。</span>
            <span>Enter 发送 · Shift + Enter 换行</span>
          </div>
        </div>
      </main>

      <aside
        v-show="processOpen"
        id="coding-process-panel"
        class="cc-panel cc-process-panel"
        aria-label="执行过程"
      >
        <div class="cc-panel-header">
          <h2>
            <AppIcon name="activity" :size="16" />
            {{
              task
                ? '第 ' +
                  (task.turn_index || turns.findIndex((item) => item.id === task.id) + 1) +
                  ' 轮过程'
                : '执行过程'
            }}
          </h2>
          <button
            class="cc-icon-button"
            type="button"
            aria-label="收起执行过程"
            @click="togglePanel('process')"
          >
            <AppIcon name="close" :size="16" />
          </button>
        </div>
        <div class="cc-panel-scroll">
          <div class="cc-process-status" role="status">
            <span class="cc-dot" :class="{ 'is-running': locked }" aria-hidden="true"></span>
            <strong>{{ currentProgress }}</strong>
            <small v-if="task">工具轮次 {{ task.round }}</small>
          </div>
          <button
            v-if="locked && !loadingConversation"
            class="cc-button"
            type="button"
            @click="stop"
          >
            <AppIcon name="stop" :size="13" />
            停止本次操作
          </button>
          <p v-if="!task && !events.length" class="cc-panel-empty">
            读取文件、搜索代码和写入结果会显示在这里。聊天正文始终留在中间。
          </p>
          <ol v-if="task?.steps?.length" class="cc-timeline" aria-label="模型执行步骤">
            <li v-for="(step, index) in task.steps" :key="task.id + '-' + index">
              <span class="cc-step-dot" aria-hidden="true"></span>
              <div class="cc-step-content">
                <strong>{{ step.tool || (step.kind === 'plan' ? '处理思路' : '模型进度') }}</strong>
                <pre>{{ step.message }}</pre>
              </div>
            </li>
          </ol>
          <details v-if="events.length" class="cc-disclosure" open>
            <summary>
              浏览器操作记录
              <small>{{ events.length }}</small>
            </summary>
            <ol class="cc-live-events">
              <li
                v-for="(step, index) in events"
                :key="index"
                :class="{ 'is-error': step.kind === 'error' }"
              >
                {{ step.message }}
              </li>
            </ol>
          </details>
          <details v-if="changes.length" class="cc-disclosure" open>
            <summary>
              <AppIcon name="pen" :size="14" />
              本地修改
              <small>{{ changes.length }}</small>
            </summary>
            <div class="cc-recovery-list">
              <button
                v-for="entry in changes"
                :key="entry.path"
                class="cc-button"
                type="button"
                :title="entry.path"
                @click="openChanges(entry.path)"
              >
                <AppIcon name="file" :size="14" />
                <span>{{ entry.path }}</span>
              </button>
            </div>
          </details>
          <details class="cc-disclosure">
            <summary>
              <AppIcon name="refresh" :size="14" />
              恢复记录
              <small>{{ journals.length }}/10</small>
            </summary>
            <p class="cc-permission-hint">
              备份仅在当前浏览器保存；恢复需重新选择原目录。重要项目仍建议使用 Git。
            </p>
            <p v-if="!journals.length" class="cc-panel-empty">暂无可恢复记录。</p>
            <div class="cc-recovery-list">
              <div v-for="journal in journals" :key="journal.key" class="cc-recovery-item">
                <strong>{{ journal.directoryName }}</strong>
                <small>
                  {{ new Date(journal.createdAt).toLocaleString('zh-CN') }} ·
                  {{ journal.entries.length }} 个文件
                </small>
                <div class="cc-recovery-actions">
                  <button
                    class="cc-button"
                    type="button"
                    :disabled="locked || !supported"
                    @click="askRecovery(journal, 'restore')"
                  >
                    恢复修改
                  </button>
                  <button
                    class="cc-button"
                    type="button"
                    :disabled="locked"
                    @click="askRecovery(journal, 'clear')"
                  >
                    清除备份
                  </button>
                </div>
              </div>
            </div>
          </details>
        </div>
      </aside>
    </div>

    <dialog ref="changesDialog" class="cc-changes-dialog" aria-labelledby="coding-changes-title">
      <header class="cc-dialog-header">
        <div>
          <h2 id="coding-changes-title">本地修改对照</h2>
          <p>这里只展示已记录的内容，不会再次读取磁盘。</p>
        </div>
        <button
          class="cc-icon-button"
          type="button"
          aria-label="关闭修改对照"
          @click="changesDialog?.close()"
        >
          <AppIcon name="close" :size="20" />
        </button>
      </header>
      <template v-if="visibleChange">
        <label for="local-change-file" class="sr-only">选择改动文件</label>
        <select id="local-change-file" v-model="selectedPath" class="cc-change-select">
          <option value="">选择改动文件</option>
          <option v-for="entry in changes" :key="entry.path" :value="entry.path">
            {{ entry.path }} ·
            {{
              entry.phase === 'applied'
                ? '已保存'
                : entry.phase === 'restored'
                  ? '已恢复'
                  : '需核对写入结果'
            }}
          </option>
        </select>
        <h3>{{ visibleChange.path }}</h3>
        <p class="cc-dialog-note">
          {{
            visibleChange.phase === 'prepared'
              ? '写入结果尚需核对，请结合浏览器操作记录与本地文件确认。'
              : visibleChange.phase === 'restored'
                ? '此条记录已执行过恢复。'
                : '本次记录已保存到本地文件。'
          }}
        </p>
        <div class="cc-diff-columns">
          <div>
            <strong>修改前{{ visibleChange.before === null ? '（新建文件）' : '' }}</strong>
            <pre>{{ visibleChange.before?.slice(0, 12000) ?? '文件原本不存在' }}</pre>
          </div>
          <div>
            <strong>本次修改内容</strong>
            <pre>{{ visibleChange.after.slice(0, 12000) }}</pre>
          </div>
        </div>
        <p class="cc-dialog-note">
          每侧最多预览 12000 字符；恢复入口位于右侧「恢复记录」，恢复前请确认原目录。
        </p>
      </template>
    </dialog>

    <dialog
      ref="consent"
      class="local-consent"
      aria-labelledby="local-consent-title"
      @cancel="locked && $event.preventDefault()"
    >
      <span class="local-consent-icon"><AppIcon name="lock" :size="25" /></span>
      <h2 id="local-consent-title">
        {{
          ['start', 'connect'].includes(consentMode)
            ? '为此项目对话连接本地文件夹'
            : consentMode === 'restore'
              ? '允许知言恢复这次文件修改？'
              : '清除这份本地恢复记录？'
        }}
      </h2>
      <template v-if="['start', 'connect'].includes(consentMode)">
        <p>
          仅针对
          <strong>当前项目对话及你接下来选择的目录</strong>
          申请以下权限：
        </p>
        <ul>
          <li>查看源码目录，读取任务需要的文本文件。</li>
          <li>直接创建、修改文件，保留修改前的本地恢复记录。</li>
          <li>把必要文件片段发送到后端及你配置的 AI 服务。</li>
        </ul>
        <p class="local-consent-scope">
          后续消息会复用此目录连接，每轮只在你发送需求后执行。停止只结束当前一轮，不断开目录；你可随时主动断开。退出账号会清除此账号记住的目录连接。不允许执行命令、安装依赖或由模型删除文件。
        </p>
        <p v-if="conversation">
          请重新选择此项目的原目录“{{
            conversation.directory_name
          }}”。若本浏览器仍有原目录记录，会核对目录本身；在新浏览器或退出账号后无原记录时无法核对完整路径，请自行确认。不同项目请新建对话，不要仅凭同名目录判断。
        </p>
        <p>
          点击后会出现浏览器目录选择／权限提示。目录连接仅记在当前浏览器；若浏览器权限失效，需重新连接，知言无法跳过浏览器授权。
        </p>
      </template>
      <template v-else-if="consentMode === 'restore'">
        <p>
          请选择“{{ recovery?.directoryName }}”的原目录。将恢复记录中
          {{ recovery?.entries.length }} 个文件的修改前内容，并删除本次新建且未被再次修改的文件。
        </p>
        <p>只处理内容与本次记录一致的文件；发现后续改动会跳过，不强行覆盖。不会递归删除文件夹。</p>
      </template>
      <template v-else>
        <p>
          只清除当前浏览器中的备份，不删除电脑上的项目文件。清除后，无法再通过这条记录撤销修改。
        </p>
      </template>
      <div class="local-consent-actions">
        <button class="coding-button" :disabled="locked" @click="consent?.close()">
          {{ consentMode === 'clear' ? '保留记录' : '拒绝' }}
        </button>
        <button class="coding-button coding-primary" :disabled="locked" @click="permitOnce">
          {{
            consentMode === 'clear'
              ? '确认清除'
              : authorizing
                ? '等待浏览器确认…'
                : consentMode === 'restore'
                  ? '允许这次恢复并选择目录'
                  : '允许此项目对话并选择目录'
          }}
        </button>
      </div>
    </dialog>
  </div>
</template>
