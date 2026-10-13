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
import AccountMenu from './AccountMenu.vue'
import AccountSettings from './AccountSettings.vue'
import { buildCodingFileTree } from '../lib/coding-view.js'
import { localCodingApi as api } from '../api/local-coding.js'
import {
  getCodingBinding,
  prepareCodingReconnect,
  reconnectCodingBinding,
  requestCodingDirectoryPermission,
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
  signingOut: { type: Boolean, default: false },
})
const emit = defineEmits(['back', 'auth-expired', 'logout', 'profile-updated', 'password-changed'])
const accountSettingsOpen = ref(false)
const accountSettingsTab = ref('profile')
const supported = localSupport()
const instruction = ref('')
const task = ref(null)
const history = ref([])
const conversations = ref([])
const projects = ref([])
const project = ref(null)
const newProjectName = ref('')
const renameDialog = ref(null)
const renameDraft = ref('')
const renameError = ref('')
const savingProjectName = ref(false)
const conversation = ref(null)
const projectConversations = computed(() =>
  conversations.value.filter((item) => item.project_id === project.value?.id),
)
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
const reconnectCandidate = shallowRef(null)
const consentError = ref('')
const canReconnect = computed(
  () =>
    ['start', 'connect'].includes(consentMode.value) &&
    reconnectCandidate.value?.owner === props.user.id &&
    reconnectCandidate.value?.conversationId === project.value?.id,
)
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
const locked = computed(
  () =>
    busy.value ||
    authorizing.value ||
    loadingConversation.value ||
    savingProjectName.value ||
    props.signingOut ||
    accountSettingsOpen.value,
)
function openAccountSettings(tab) {
  if (locked.value || activeHistory.value.length || !props.active) return
  accountSettingsTab.value = tab
  accountSettingsOpen.value = true
}
function logoutFromCoding() {
  if (
    (busy.value || authorizing.value || activeHistory.value.length) &&
    !window.confirm(
      '退出登录会停止本页面的文件操作并清除保存的目录连接，已经写入的文件不会自动撤销。确定退出吗？',
    )
  )
    return
  stop() // Close the local execution gate before the logout request starts.
  emit('logout')
}
function modelSettingsUpdated() {
  window.dispatchEvent(new Event('zhiyan-model-settings-updated'))
}
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
  if (savingProjectName.value) return '正在保存项目名称'
  if (loadingConversation.value) return '正在打开编程对话'
  if (authorizing.value)
    return canReconnect.value ? '等待浏览器恢复目录授权' : '正在检查或连接项目目录'
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
  if (accountSettingsOpen.value) return
  if (
    !panelOverlay.value ||
    consent.value?.open ||
    changesDialog.value?.open ||
    renameDialog.value?.open
  )
    return
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
  task.value = null
  pendingQuestion.value = ''
  instruction.value = ''
  rememberConversation(null)
  directoryName.value = project.value?.directory_name || '尚未选择工作目录'
  if (!boundRoot.value) files.value = []
  changes.value = []
  events.value = []
  selectedPath.value = ''
  fileFilter.value = ''
  collapsedFolders.value = new Set()
  notice.value = project.value ? '新的独立对话：共享此项目的文件，不带入其他对话的聊天记录。' : ''
  error.value = ''
  closePanels()
  nextTick(() => textarea.value?.focus())
}
let alive = true
let generation = 0
let viewGeneration = 0
let refreshGeneration = 0
let capabilityGeneration = 0
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
function rememberProject(id) {
  try {
    const key = `zhiyan-coding-project:${props.user.id}`
    if (id) sessionStorage.setItem(key, id)
    else sessionStorage.removeItem(key)
  } catch {
    // The selected project still works when session storage is unavailable.
  }
}
async function selectProject(id) {
  if (locked.value) return
  const selected = projects.value.find((item) => item.id === id)
  if (!selected) return
  const owner = props.user.id
  project.value = selected
  boundRoot.value = null
  files.value = []
  newTaskView()
  const view = viewGeneration
  const current = () => alive && props.active && props.user.id === owner && view === viewGeneration
  rememberProject(selected.id)
  loadingConversation.value = true
  try {
    const root = supported ? await getCodingBinding(owner, selected.id) : null
    if (!current()) return
    boundRoot.value = root ? markRaw(root) : null
    notice.value = root
      ? '已切换项目，可以打开已有对话或直接开始新对话，无需重新选择目录。'
      : '已切换项目。目录连接不可用，请重新连接原目录后继续编程。'
    openHistoryList()
  } catch (cause) {
    if (current()) failure(cause)
  } finally {
    if (alive && view === viewGeneration) loadingConversation.value = false
  }
}
async function ensureConversation(run) {
  if (conversation.value) return
  const selected = project.value
  const owner = props.user.id
  if (!selected) throw new Error('请先连接项目目录。')
  const created = await api.createConversation({
    client_conversation_id: crypto.randomUUID(),
    project_id: selected.id,
    directory_name: selected.directory_name,
  })
  if (
    !alive ||
    !props.active ||
    run !== generation ||
    props.user.id !== owner ||
    project.value?.id !== selected.id
  )
    throw new DOMException('Stopped', 'AbortError')
  conversation.value = created
  turns.value = []
  rememberConversation(created.id)
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
  if (locked.value || !project.value) return
  const id = project.value.id
  const owner = props.user.id
  boundRoot.value = null
  authorizing.value = true
  try {
    await forgetCodingBinding(owner, id)
    if (alive)
      notice.value =
        '已断开整个项目的目录访问。所有对话和本地文件保留，继续编程时需要重新连接原目录。'
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
  const modelRevision = capabilityGeneration
  const results = await Promise.allSettled([
    api.list(),
    listLocalJournals(owner),
    api.capabilities(),
    api.listConversations(),
    api.listProjects(),
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
  if (modelRevision === capabilityGeneration) {
    if (results[2].status === 'fulfilled') capabilities.value = results[2].value
    else failure(results[2].reason)
  }
  if (results[3].status === 'fulfilled') {
    conversations.value = results[3].value.items
    const current = conversations.value.find((item) => item.id === conversation.value?.id)
    if (current) conversation.value = current
  } else failure(results[3].reason)
  if (results[4].status === 'fulfilled') {
    projects.value = results[4].value.items
    const current = projects.value.find((item) => item.id === project.value?.id)
    if (current) project.value = current
  } else failure(results[4].reason)
}

async function askStart() {
  if (locked.value || !instruction.value.trim() || !supported || activeHistory.value.length) return
  pendingInstruction = instruction.value.trim()
  consentMode.value = 'start'
  if (!project.value) newProjectName.value = ''
  recovery.value = null
  error.value = ''
  notice.value = ''
  const run = ++generation
  const owner = props.user.id
  reconnectCandidate.value = null
  consentError.value = ''
  if (project.value) {
    authorizing.value = true
    try {
      // Recheck both the app's binding and browser permission, without prompting.
      const currentRoot = await getCodingBinding(owner, project.value.id)
      if (!alive || !props.active || run !== generation || props.user.id !== owner) return
      if (currentRoot) {
        boundRoot.value = markRaw(currentRoot)
        authorizing.value = false
        await executeInDirectory(currentRoot, run)
        return
      }
      boundRoot.value = null
      const candidate = await prepareCodingReconnect(owner, project.value.id)
      if (!alive || !props.active || run !== generation || props.user.id !== owner) return
      reconnectCandidate.value = candidate ? markRaw(candidate) : null
      notice.value = candidate
        ? '原目录记录仍在，确认恢复访问即可，无需重新选择文件夹。'
        : '没有可恢复的目录记录，请重新选择此项目的原目录。'
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
async function askConnect(newProject = false) {
  if (locked.value || !supported) return
  const run = ++generation
  pendingInstruction = ''
  consentMode.value = newProject ? 'project' : 'connect'
  if (newProject || !project.value) newProjectName.value = ''
  recovery.value = null
  error.value = ''
  consentError.value = ''
  reconnectCandidate.value = null
  const owner = props.user.id
  if (!newProject && project.value) {
    authorizing.value = true
    try {
      const candidate = await prepareCodingReconnect(owner, project.value.id)
      if (!alive || !props.active || run !== generation || props.user.id !== owner) return
      reconnectCandidate.value = candidate ? markRaw(candidate) : null
    } catch (cause) {
      failure(cause)
      return
    } finally {
      authorizing.value = false
    }
  }
  await nextTick()
  if (!alive || !props.active || run !== generation) return
  if (!consent.value?.open) consent.value?.showModal()
}
async function askRecovery(journal, mode) {
  if (locked.value) return
  const run = ++generation
  recovery.value = structuredClone(toRaw(journal))
  consentMode.value = mode
  reconnectCandidate.value = null
  consentError.value = ''
  await nextTick()
  if (!alive || !props.active || run !== generation) return
  consent.value.showModal()
}

async function permitOnce(forcePicker = false) {
  if (locked.value) return
  const run = ++generation
  const mode = consentMode.value
  const owner = props.user.id
  const view = viewGeneration
  const candidate = !forcePicker && canReconnect.value ? reconnectCandidate.value : null
  let executionOwnsCleanup = false
  let keepConsentOpen = false
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
  consentError.value = ''
  try {
    // Must be the direct result of this trusted click, before network/IDB awaits.
    let root
    if (candidate) {
      const permission = await requestCodingDirectoryPermission(candidate.handle)
      assertCurrent()
      if (permission !== 'granted') {
        keepConsentOpen = true
        consentError.value =
          '浏览器尚未允许目录读写，未执行任务。可重试；如果权限已被禁止，请先检查浏览器的站点权限设置。'
        return
      }
      root = await reconnectCodingBinding(owner, project.value.id, candidate)
      if (!current()) {
        await forgetCodingBinding(owner, candidate.conversationId)
        return
      }
    } else {
      root = await window.showDirectoryPicker({
        mode: 'readwrite',
        id: 'zhiyan-coding-project',
      })
    }
    assertCurrent()
    if (['start', 'connect', 'project'].includes(mode)) {
      let currentProject = mode === 'project' ? null : project.value
      if (!currentProject) {
        currentProject = await api.createProject({
          client_project_id: crypto.randomUUID(),
          directory_name: root.name.slice(0, 120),
          name: newProjectName.value.trim(),
        })
        assertCurrent()
      }
      if (root.name.slice(0, 120) !== currentProject.directory_name)
        throw new Error(
          `请选择此项目原先的目录“${currentProject.directory_name}”。不同目录请添加新项目。`,
        )
      // The binding helper checks handle identity, never just a matching folder name.
      if (!candidate) await saveCodingBinding(owner, currentProject.id, root)
      if (!current()) {
        await forgetCodingBinding(owner, currentProject.id)
        return
      }
      if (project.value?.id !== currentProject.id) {
        conversation.value = null
        turns.value = []
        task.value = null
        files.value = []
        changes.value = []
        events.value = []
        rememberConversation(null)
        if (mode === 'project') instruction.value = ''
      }
      project.value = currentProject
      rememberProject(currentProject.id)
      boundRoot.value = markRaw(root)
      directoryName.value = root.name
      consent.value.close()
      if (mode !== 'start') {
        notice.value = '项目目录已连接。此项目下的所有对话复用目录，无需每次选择或授权。'
        return
      }
      executionOwnsCleanup = true
      authorizing.value = false
      await executeInDirectory(root, run)
      return
    }
    consent.value.close()
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
    if (candidate && current()) {
      keepConsentOpen = true
      consentError.value =
        cause.name === 'NotFoundError'
          ? '原目录已不存在或无法定位，请确认文件夹位置，必要时重新选择原目录。'
          : cause.name === 'SecurityError'
            ? '浏览器未接受本次授权请求，请再次点击恢复访问；若仍被拒绝，请检查站点权限设置。'
            : cause.name === 'AbortError'
              ? '已取消授权，未启动任务。'
              : cause.message || '恢复目录访问失败，可重试或重新选择原目录。'
    } else if (cause.name === 'AbortError' && alive)
      notice.value = '目录选择或本次操作已取消，未执行新的文件操作。'
    else failure(cause)
  } finally {
    if (!executionOwnsCleanup) {
      session?.revoke()
      session = null
      restoreController = null
      requestController?.abort()
      if (!keepConsentOpen || !current()) {
        consent.value?.close()
        reconnectCandidate.value = null
      }
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
      await ensureConversation(run)
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
  const projectId = project.value?.id
  if (
    !conversationId ||
    !projectId ||
    conversation.value.project_id !== projectId ||
    boundRoot.value !== root
  )
    throw new Error('请先连接此对话所属的项目目录。')
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
    project.value?.id === projectId &&
    conversation.value?.id === conversationId &&
    boundRoot.value === root
  const assertCurrent = () => {
    if (!current()) throw new DOMException('Stopped', 'AbortError')
  }
  // Creating a new conversation can take time; recheck the project grant before scanning.
  const activeRoot = await getCodingBinding(owner, projectId)
  assertCurrent()
  if (!activeRoot || !(await root.isSameEntry(activeRoot))) {
    boundRoot.value = null
    throw new Error('项目目录连接已失效，请重新连接原目录。')
  }
  assertCurrent()
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
  event('使用所属项目已连接的目录，重新查看最新目录结构；暂不读取全部文件正文。')
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
        throw new Error('本轮执行达到时间上限，已停止文件操作。项目目录连接保留，可继续发送需求。')
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
        // A different tab may have disconnected this project or cleared login bindings.
        const activeRoot = await getCodingBinding(owner, projectId)
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
  reconnectCandidate.value = null
  changesDialog.value?.close()
  renameDialog.value?.close()
  // Merely viewing an unfinished history item must not cancel another tab's execution.
  if (session && task.value && !terminal.has(task.value.status)) api.stopOnLeave(task.value.id)
  if (busy.value || authorizing.value)
    notice.value = '已停止本轮文件操作，正在收尾。项目目录连接保留，已保存的改动不会自动撤销。'
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
  project.value = null
  rememberProject(null)
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
    project.value = projects.value.find((entry) => entry.id === detail.project_id) || {
      id: detail.project_id,
      directory_name: detail.directory_name,
    }
    rememberProject(detail.project_id)
    turns.value = detail.tasks || []
    task.value = turns.value.at(-1) || null
    events.value = taskEvents.get(task.value?.id) || []
    changes.value = task.value ? changesFor(task.value.id) : []
    files.value = changes.value.map((entry) => ({ path: entry.path }))
    fileFilter.value = ''
    collapsedFolders.value = new Set()
    directoryName.value = detail.directory_name
    rememberConversation(detail.id)
    const root = supported ? await getCodingBinding(owner, detail.project_id) : null
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
    if (!current()) return
    await refresh()
    if (!current()) return
    loadingConversation.value = false
    if (conversation.value?.id === item.id) newTaskView()
    notice.value = '已删除此对话；项目目录连接、其他对话、本地文件和恢复记录均保留。'
  } catch (cause) {
    if (current()) failure(cause)
  } finally {
    if (current()) loadingConversation.value = false
  }
}
async function openProjectRename() {
  if (locked.value || !project.value) return
  const run = ++generation
  renameDraft.value = project.value.name || project.value.directory_name
  renameError.value = ''
  await nextTick()
  if (alive && props.active && run === generation) renameDialog.value?.showModal()
}
async function saveProjectName() {
  if (locked.value || !project.value) return
  const selected = project.value
  const owner = props.user.id
  const run = ++generation
  savingProjectName.value = true
  renameError.value = ''
  const current = () =>
    alive &&
    props.active &&
    props.user.id === owner &&
    run === generation &&
    project.value?.id === selected.id
  try {
    const updated = await api.renameProject(selected.id, { name: renameDraft.value.trim() })
    if (!current()) return
    ++refreshGeneration // Ignore list snapshots requested before this successful rename.
    project.value = updated
    projects.value = projects.value.map((entry) => (entry.id === updated.id ? updated : entry))
    renameDialog.value?.close()
    notice.value = '项目名称已更新，文件夹、目录授权和对话记录均保持不变。'
  } catch (cause) {
    if (current()) {
      renameError.value = cause.message || '项目改名失败，请稍后重试。'
      if (cause.status === 401) failure(cause)
    }
  } finally {
    savingProjectName.value = false
  }
}
async function removeProject() {
  if (locked.value || !project.value) return
  const selected = project.value
  if (
    !window.confirm(
      `删除项目“${selected.name || selected.directory_name}”（目录：${selected.directory_name}）及其全部对话和执行记录？本地文件和浏览器恢复记录不会删除。`,
    )
  )
    return
  const owner = props.user.id
  const view = ++viewGeneration
  loadingConversation.value = true
  try {
    await api.removeProject(selected.id)
    let bindingError = null
    try {
      await deleteCodingBinding(owner, selected.id)
    } catch (cause) {
      bindingError = cause
    }
    if (!alive || props.user.id !== owner || view !== viewGeneration) return
    await refresh()
    if (!alive || props.user.id !== owner || view !== viewGeneration) return
    project.value = null
    boundRoot.value = null
    rememberProject(null)
    loadingConversation.value = false
    newTaskView()
    notice.value = '项目及其对话记录已删除；本地文件和浏览器恢复记录保留。'
    if (bindingError)
      error.value =
        '浏览器中的旧目录记录未能清除；项目已不可执行，请在浏览器站点设置中撤销目录权限。'
  } catch (cause) {
    failure(cause)
  } finally {
    if (alive) loadingConversation.value = false
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
    if (!active) {
      accountSettingsOpen.value = false
      stop()
    } else refresh()
  },
)
watch(instruction, () => nextTick(resizeInput))
watch([turns, pendingQuestion, locked], () => scrollChat(), { deep: true })
async function refreshModelCapabilities() {
  const revision = ++capabilityGeneration
  try {
    const value = await api.capabilities()
    if (alive && revision === capabilityGeneration) capabilities.value = value
  } catch {
    // Existing task/folder bindings are unaffected by a metadata refresh failure.
  }
}
onMounted(async () => {
  const initialGeneration = generation
  const initialView = viewGeneration
  resizeInput()
  window.addEventListener('resize', fitPanels)
  window.addEventListener('pagehide', stop)
  window.addEventListener('beforeunload', beforeLeave)
  window.addEventListener('zhiyan-model-settings-updated', refreshModelCapabilities)
  await refresh()
  if (!alive || props.user.id !== componentOwner) return
  try {
    const id = sessionStorage.getItem(`zhiyan-coding-conversation:${componentOwner}`)
    const item = conversations.value.find((entry) => entry.id === id)
    const projectId = sessionStorage.getItem(`zhiyan-coding-project:${componentOwner}`)
    if (
      props.active &&
      !conversation.value &&
      !locked.value &&
      !consent.value?.open &&
      !instruction.value.trim() &&
      generation === initialGeneration &&
      viewGeneration === initialView
    ) {
      if (item) await showConversation(item)
      else if (projectId) await selectProject(projectId)
    }
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
  window.removeEventListener('zhiyan-model-settings-updated', refreshModelCapabilities)
})
</script>

<template>
  <div class="coding-chat-shell" @keydown.esc="dismissSidePanel">
    <AccountSettings
      v-if="accountSettingsOpen && active"
      :user="user"
      :initial-tab="accountSettingsTab"
      @close="accountSettingsOpen = false"
      @updated="emit('profile-updated', $event)"
      @password-changed="emit('password-changed', $event)"
      @model-settings-updated="modelSettingsUpdated"
    />
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
        <p :title="project?.name ? `${project.name} · ${directoryName}` : directoryName">
          {{
            project?.name
              ? `${project.name} · ${directoryName}`
              : project || turns.length
                ? directoryName
                : '知言 AI · 在你的项目里工作'
          }}
        </p>
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
        <AccountMenu
          class="cc-account-menu"
          menu-id="coding-account-dropdown"
          :user="user"
          :signing-out="signingOut"
          :settings-disabled="locked || activeHistory.length > 0"
          settings-disabled-hint="请先完成当前操作；编程任务执行中请等待结束或先停止，再修改账号或模型配置。"
          show-model-settings
          @settings="openAccountSettings"
          @logout="logoutFromCoding"
        />
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
          <label for="coding-project-select" class="cc-project-label">当前项目</label>
          <select
            id="coding-project-select"
            class="cc-project-select"
            :value="project?.id || ''"
            :disabled="locked || !projects.length"
            @change="selectProject($event.target.value)"
          >
            <option value="" disabled>选择项目</option>
            <option v-for="entry in projects" :key="entry.id" :value="entry.id">
              {{ entry.name || entry.directory_name }}
            </option>
          </select>
          <div class="cc-binding-actions">
            <button
              class="cc-button"
              type="button"
              :disabled="locked || !supported"
              @click="askConnect(true)"
            >
              <AppIcon name="plus" :size="14" />
              添加项目
            </button>
            <button
              v-if="project"
              class="cc-button"
              type="button"
              :disabled="locked"
              @click="openProjectRename"
            >
              重命名
            </button>
            <button
              v-if="project"
              class="cc-button"
              type="button"
              :disabled="locked"
              @click="removeProject"
            >
              删除项目
            </button>
          </div>
          <div class="cc-folder-chip" :title="directoryName">
            <AppIcon name="folder" :size="17" />
            <span>{{ directoryName }}</span>
          </div>
          <p class="cc-permission-hint">
            <AppIcon name="lock" :size="12" />
            {{
              boundRoot ? '已连接 · 此项目所有对话共用目录' : '未连接 · 仅查看已有聊天和修改记录'
            }}
          </p>
          <div class="cc-binding-actions">
            <button
              v-if="!boundRoot"
              class="cc-button"
              type="button"
              :disabled="locked || !supported"
              @click="askConnect(false)"
            >
              <AppIcon name="folder" :size="14" />
              {{ project ? '恢复目录访问' : '连接项目目录' }}
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
          <button
            class="cc-button"
            type="button"
            :disabled="locked || !project"
            @click="newTaskView"
          >
            <AppIcon name="plus" :size="15" />
            新建项目对话
          </button>
          <details ref="historyDetails" class="cc-disclosure" open>
            <summary>
              <AppIcon name="chat" :size="14" />
              项目对话
              <small>{{ projectConversations.length }}</small>
            </summary>
            <button class="cc-button" type="button" :disabled="locked" @click="refresh">
              <AppIcon name="refresh" :size="13" />
              刷新记录
            </button>
            <p v-if="!projectConversations.length" class="cc-panel-empty">
              {{ project ? '发送第一条需求后，对话会保存在此项目下。' : '请先选择或添加项目。' }}
            </p>
            <nav class="cc-history-list" aria-label="项目编程对话历史">
              <div
                v-for="item in projectConversations"
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
      ref="renameDialog"
      class="local-consent"
      aria-labelledby="project-rename-title"
      @cancel="savingProjectName && $event.preventDefault()"
    >
      <form @submit.prevent="saveProjectName">
        <h2 id="project-rename-title">重命名项目</h2>
        <label class="cc-project-label" for="project-rename-input">项目名称</label>
        <input
          id="project-rename-input"
          v-model="renameDraft"
          class="cc-project-select"
          maxlength="120"
          :disabled="savingProjectName"
          autocomplete="off"
          autofocus
          placeholder="留空自动命名，例如：项目 1"
          aria-describedby="project-rename-help"
        />
        <p id="project-rename-help">
          最多 120 个字符。留空会分配下一个默认编号，仅修改显示名称，不修改本地文件夹。
        </p>
        <p v-if="renameError" role="alert">{{ renameError }}</p>
        <div class="local-consent-actions">
          <button
            class="coding-button"
            type="button"
            :disabled="savingProjectName"
            @click="renameDialog?.close()"
          >
            取消
          </button>
          <button class="coding-button coding-primary" type="submit" :disabled="savingProjectName">
            {{ savingProjectName ? '保存中…' : '保存名称' }}
          </button>
        </div>
      </form>
    </dialog>

    <dialog
      ref="consent"
      class="local-consent"
      aria-labelledby="local-consent-title"
      @cancel="locked && $event.preventDefault()"
      @close="reconnectCandidate = null"
    >
      <span class="local-consent-icon"><AppIcon name="lock" :size="25" /></span>
      <h2 id="local-consent-title">
        {{
          ['start', 'connect', 'project'].includes(consentMode)
            ? canReconnect
              ? '恢复此项目的目录访问'
              : '为项目连接本地文件夹'
            : consentMode === 'restore'
              ? '允许知言恢复这次文件修改？'
              : '清除这份本地恢复记录？'
        }}
      </h2>
      <template v-if="['start', 'connect', 'project'].includes(consentMode)">
        <template v-if="!project || consentMode === 'project'">
          <label class="cc-project-label" for="new-project-name">项目名称（可选）</label>
          <input
            id="new-project-name"
            v-model="newProjectName"
            class="cc-project-select"
            maxlength="120"
            :disabled="locked"
            autocomplete="off"
            placeholder="例如：个人网站；留空自动命名"
            aria-describedby="new-project-name-help"
          />
          <p id="new-project-name-help">
            不填写时按“项目 1、项目 2、项目 3……”依次命名，之后也可以修改。
          </p>
        </template>
        <p>
          仅针对
          <strong>
            {{
              canReconnect
                ? '此项目下的所有对话及已保存的原目录'
                : '此项目下的所有对话及你接下来选择的目录'
            }}
          </strong>
          申请以下权限：
        </p>
        <ul>
          <li>查看源码目录，读取任务需要的文本文件。</li>
          <li>直接创建、修改文件，保留修改前的本地恢复记录。</li>
          <li>把必要文件片段发送到后端及你配置的 AI 服务。</li>
        </ul>
        <p class="local-consent-scope">
          同一项目内的新对话和后续消息会复用此目录连接，但各自的聊天记录独立。每轮只在你发送需求后执行。停止只结束当前一轮；断开目录对整个项目生效。退出账号会清除此账号记住的目录连接。不允许执行命令、安装依赖或由模型删除文件。
        </p>
        <p v-if="canReconnect">
          已记住原目录“{{
            project.directory_name
          }}”。点击「恢复访问」后，只针对这个目录请求浏览器读写许可，不打开文件夹选择器。你主动断开过的连接也只会在这次明确确认后恢复。
        </p>
        <p v-else-if="project && consentMode !== 'project'">
          请重新选择此项目的原目录“{{
            project.directory_name
          }}”。若本浏览器仍有原目录记录，会核对目录本身；在新浏览器或退出账号后无原记录时无法核对完整路径，请自行确认。不同目录请添加新项目，不要仅凭同名目录判断。
        </p>
        <p v-if="!canReconnect">
          点击后会出现浏览器目录选择／权限提示。目录连接仅记在当前浏览器；若浏览器权限失效，需重新连接，知言无法跳过浏览器授权。
        </p>
        <p v-else>
          如果拒绝授权，不会启动任务，也不会自动改为选择文件夹。原目录无法恢复时，可以主动点击「重新选择原目录」；仍需核对它是否是原来的文件夹。
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
      <p v-if="consentError" role="alert">{{ consentError }}</p>
      <div class="local-consent-actions">
        <button class="coding-button" :disabled="locked" @click="consent?.close()">
          {{ consentMode === 'clear' ? '保留记录' : '拒绝' }}
        </button>
        <button
          v-if="canReconnect"
          class="coding-button"
          :disabled="locked"
          @click="permitOnce(true)"
        >
          重新选择原目录
        </button>
        <button class="coding-button coding-primary" :disabled="locked" @click="permitOnce()">
          {{
            consentMode === 'clear'
              ? '确认清除'
              : authorizing
                ? '等待浏览器确认…'
                : consentMode === 'restore'
                  ? '允许这次恢复并选择目录'
                  : canReconnect
                    ? '恢复访问'
                    : '允许此项目并选择目录'
          }}
        </button>
      </div>
    </dialog>
  </div>
</template>
