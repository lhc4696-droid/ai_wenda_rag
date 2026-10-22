<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  deleteModelSettings,
  getModelSettings,
  saveModelSettings,
  testModelConnection,
} from '../api/model-settings.js'

const emit = defineEmits(['busy', 'updated'])
const metadata = ref(null)
const source = ref('default')
const baseUrl = ref('')
const apiKey = ref('')
const chatModel = ref('')
const codingModel = ref('')
const visionModel = ref('')
const loading = ref(true)
const busy = ref('')
const error = ref('')
const feedback = ref('')
const confirmDelete = ref(false)
const needsRefresh = ref(false)
const testResult = ref(null)
const loadController = new AbortController()
let testController = null
let alive = true

const locked = computed(() => loading.value || Boolean(busy.value) || needsRefresh.value)
const customUnavailable = computed(() => !metadata.value?.storage_ready)
const endpointChanged = computed(
  () => normalizeUrl(baseUrl.value) !== normalizeUrl(metadata.value?.base_url || ''),
)
const testing = computed(() => busy.value === 'test-chat' || busy.value === 'test-coding')

// A successful probe applies only to the draft that was actually submitted.
// Do not retain API keys in a result fingerprint or browser storage.
watch([source, baseUrl, apiKey, chatModel, codingModel, visionModel], () => {
  testResult.value = null
})

function normalizeUrl(value) {
  try {
    return new URL(value.trim()).toString().replace(/\/+$/, '')
  } catch {
    return value.trim().replace(/\/+$/, '')
  }
}

function applyMetadata(value) {
  metadata.value = value
  source.value = value.enabled ? 'custom' : 'default'
  baseUrl.value = value.base_url || ''
  chatModel.value = value.chat_model || ''
  codingModel.value = value.coding_model || ''
  visionModel.value = value.vision_model || ''
  apiKey.value = ''
  confirmDelete.value = false
}

function setBusy(value) {
  busy.value = value
  emit('busy', Boolean(value))
}

async function load() {
  if (busy.value) return
  loading.value = true
  error.value = feedback.value = ''
  testResult.value = null
  apiKey.value = ''
  try {
    const result = await getModelSettings(loadController.signal)
    if (!alive) return
    applyMetadata(result)
    needsRefresh.value = false
  } catch (cause) {
    if (alive) error.value = cause.message
  } finally {
    if (alive) loading.value = false
  }
}

function changeSource(value) {
  if (locked.value) return
  source.value = value
  apiKey.value = ''
  error.value = feedback.value = ''
  confirmDelete.value = false
}

function setDeleteConfirmation(value) {
  if (locked.value) return
  confirmDelete.value = value
  apiKey.value = ''
}

function validateCustomSettings() {
  if (customUnavailable.value) return '管理员尚未配置模型密钥加密功能，请先使用项目默认模型。'
  let url
  try {
    url = new URL(baseUrl.value.trim())
  } catch {
    return '请填写完整的 HTTPS Base URL。'
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash)
    return 'Base URL 必须是 HTTPS 地址，不能包含账号、密码、查询参数或 # 片段。'
  if (/\/chat\/completions\/?$/i.test(url.pathname))
    return '请填写基础地址（通常以 /v1 结尾），不要附加 /chat/completions。'
  if (!chatModel.value.trim()) return '请填写普通聊天模型的完整名称。'
  if (
    [chatModel.value, codingModel.value, visionModel.value].some((value) =>
      /[\u0000-\u001f\u007f]/.test(value),
    )
  )
    return '模型名称不能包含换行或控制字符。'
  if ((!metadata.value?.has_api_key || endpointChanged.value) && !apiKey.value.trim())
    return endpointChanged.value && metadata.value?.has_api_key
      ? '服务地址已改变，请重新填写这个服务的 API Key；旧密钥不会转发给新地址。'
      : '请填写此服务的 API Key。'
  if (apiKey.value && /\s/.test(apiKey.value.trim())) return 'API Key 不能包含空格或换行。'
  return ''
}

function handleFailure(cause) {
  error.value = cause.message
  if (cause.status === 0) {
    needsRefresh.value = true
    error.value += ' 保存结果暂未确认，请先重新读取配置，再决定是否重试。'
  }
}

async function testConnection(target) {
  if (locked.value || source.value !== 'custom' || !metadata.value) return
  error.value = feedback.value = ''
  testResult.value = null
  error.value = validateCustomSettings()
  if (error.value) return

  const settings = {
    target,
    base_url: baseUrl.value.trim(),
    chat_model: chatModel.value.trim(),
    coding_model: codingModel.value.trim(),
    ...(apiKey.value.trim() ? { api_key: apiKey.value.trim() } : {}),
  }
  const requestedModel =
    target === 'coding' ? settings.coding_model || settings.chat_model : settings.chat_model
  const startedAt = Date.now()
  testController = new AbortController()
  setBusy(`test-${target}`)
  try {
    const result = await testModelConnection(settings, testController.signal)
    if (!alive) return
    testResult.value = {
      state: result.ok ? 'success' : 'failure',
      target: result.target,
      model: result.model,
      elapsedMs: result.elapsed_ms,
      message: result.message,
      errorCode: result.error_code || '',
      requestId: result.request_id || '',
      inputTokens: result.input_tokens,
      outputTokens: result.output_tokens,
    }
  } catch (cause) {
    if (!alive) return
    const uncertain = cause.status === 0 || cause.name === 'AbortError'
    testResult.value = {
      state: uncertain ? 'unknown' : 'failure',
      target,
      model: requestedModel,
      elapsedMs: Date.now() - startedAt,
      message: uncertain
        ? `${cause.message} 本次结果未确认，请稍后再试；请求可能已发出并产生费用，配置没有保存。`
        : `${cause.message}${cause.retryAfter > 0 ? ` 请在 ${cause.retryAfter} 秒后重试。` : ''}`,
      errorCode: cause.errorCode || '',
      requestId: cause.requestId || '',
      inputTokens: null,
      outputTokens: null,
    }
  } finally {
    // The input remains available for an explicit Save, but the extra request copy does not.
    delete settings.api_key
    testController = null
    setBusy('')
  }
}

function formatDuration(milliseconds) {
  return Number.isFinite(milliseconds) ? `${(milliseconds / 1000).toFixed(2)} 秒` : '—'
}

async function save() {
  if (locked.value || !metadata.value) return
  error.value = feedback.value = ''
  if (source.value === 'custom') {
    error.value = validateCustomSettings()
    if (error.value) return
  }
  const settings =
    source.value === 'default'
      ? { enabled: false }
      : {
          enabled: true,
          base_url: baseUrl.value.trim(),
          chat_model: chatModel.value.trim(),
          coding_model: codingModel.value.trim(),
          vision_model: visionModel.value.trim(),
          ...(apiKey.value.trim() ? { api_key: apiKey.value.trim() } : {}),
        }
  setBusy('save')
  try {
    const result = await saveModelSettings(settings)
    if (!alive) return
    applyMetadata(result)
    emit('updated', result)
    feedback.value = result.enabled
      ? '配置已保存，后续聊天与编程任务将使用你的模型。保存操作本身不会发送模型请求，可使用下方“测试连接”检查可用性。'
      : '已切回项目默认模型。已保存的个人配置仍然保留，Embedding 配置不变。'
  } catch (cause) {
    if (alive) handleFailure(cause)
  } finally {
    apiKey.value = ''
    // Avoid keeping a second copy of the key in the submitted object after completion.
    delete settings.api_key
    setBusy('')
  }
}

async function remove() {
  if (locked.value || !confirmDelete.value) return
  error.value = feedback.value = ''
  setBusy('delete')
  try {
    const result = await deleteModelSettings()
    if (!alive) return
    applyMetadata(result)
    emit('updated', result)
    feedback.value = '个人模型配置和已保存的密钥已删除，后续请求将使用项目默认模型。'
  } catch (cause) {
    if (alive) handleFailure(cause)
  } finally {
    apiKey.value = ''
    setBusy('')
  }
}

onMounted(load)
onUnmounted(() => {
  alive = false
  loadController.abort()
  testController?.abort()
  apiKey.value = ''
})
</script>

<template>
  <div class="model-settings" :aria-busy="loading || Boolean(busy)">
    <p v-if="loading" class="account-hint" role="status">正在读取你的模型配置…</p>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <p v-else-if="feedback" class="auth-notice" role="status">{{ feedback }}</p>
    <button
      v-if="!loading && (!metadata || needsRefresh)"
      type="button"
      class="model-secondary"
      :disabled="Boolean(busy)"
      @click="load"
    >
      重新读取配置
    </button>

    <form v-if="metadata && !loading" class="model-form" novalidate @submit.prevent="save">
      <fieldset class="model-source" :disabled="locked">
        <legend>这个账号使用的模型来源</legend>
        <label :class="{ selected: source === 'default' }">
          <input
            type="radio"
            name="model-source"
            value="default"
            :checked="source === 'default'"
            @change="changeSource('default')"
          />
          <span>
            <strong>项目默认</strong>
            <small>使用管理员配置</small>
          </span>
        </label>
        <label :class="{ selected: source === 'custom' }">
          <input
            type="radio"
            name="model-source"
            value="custom"
            :checked="source === 'custom'"
            :disabled="customUnavailable"
            @change="changeSource('custom')"
          />
          <span>
            <strong>我的 API</strong>
            <small>由你的平台账号计费</small>
          </span>
        </label>
      </fieldset>

      <div v-if="customUnavailable" class="model-storage-warning">
        <p class="account-warning" role="status">
          服务端密钥加密尚未就绪，暂时不能使用“我的 API”。请确认后端环境已安装 cryptography、
          MODEL_CONFIG_SECRET 已配置为有效的加密密钥，再重启后端；仅重启不会自动补齐这些配置。
          项目默认模型仍可使用。
        </p>
        <button type="button" class="model-secondary" :disabled="locked" @click="load">
          重新检查配置
        </button>
        <p class="account-hint">这里只重新读取配置状态，不会调用模型。</p>
      </div>

      <div v-if="source === 'default'" class="model-defaults">
        <div>
          <span>普通聊天</span>
          <strong>{{ metadata.default_chat_model || '未配置' }}</strong>
        </div>
        <div>
          <span>Coding Agent</span>
          <strong>
            {{ metadata.default_coding_model || metadata.default_chat_model || '未配置' }}
          </strong>
        </div>
        <div>
          <span>图片理解</span>
          <strong>{{ metadata.default_vision_model || '未配置' }}</strong>
        </div>
        <p class="account-hint">
          切换来源后，点击下方保存才会生效。切回默认不会删除已保存的个人密钥。
        </p>
      </div>

      <template v-else>
        <p class="account-hint">
          支持 OpenAI 兼容的 Chat Completions 接口。不同平台的原生接口不一定兼容；Coding Agent
          所用模型还需支持工具调用。
        </p>
        <div class="auth-field">
          <label for="model-base-url">Base URL</label>
          <input
            id="model-base-url"
            v-model="baseUrl"
            type="url"
            autocomplete="off"
            autocapitalize="none"
            :spellcheck="false"
            maxlength="2048"
            placeholder="https://api.example.com/v1"
            :disabled="locked || customUnavailable"
            aria-describedby="model-base-hint"
            required
          />
          <small id="model-base-hint">
            填写平台提供的基础地址，不含 /chat/completions。仅支持公网 HTTPS，不支持 localhost
            或内网地址。Clash/TUN 环境可由部署者配置受控代理；这里仍填写模型平台地址，不填代理端口。
          </small>
        </div>
        <div class="auth-field">
          <label for="model-api-key">
            API Key
            <span v-if="metadata.has_api_key" class="model-key-saved">已保存密钥</span>
          </label>
          <input
            id="model-api-key"
            v-model="apiKey"
            type="password"
            name="personal-model-api-key"
            autocomplete="new-password"
            autocapitalize="none"
            :spellcheck="false"
            maxlength="2048"
            :placeholder="
              metadata.has_api_key && !endpointChanged
                ? '留空保留原密钥；输入新密钥以替换'
                : '粘贴这个平台的 API Key'
            "
            :disabled="locked || customUnavailable"
            aria-describedby="model-key-hint"
          />
          <small id="model-key-hint">
            密钥仅提交到后端加密保存，不会回传到页面。修改 Base URL 必须重新输入密钥。
          </small>
        </div>
        <div class="auth-field">
          <label for="model-chat">普通聊天模型</label>
          <input
            id="model-chat"
            v-model="chatModel"
            autocomplete="off"
            autocapitalize="none"
            :spellcheck="false"
            maxlength="200"
            placeholder="填写平台支持的模型 ID"
            :disabled="locked || customUnavailable"
            required
          />
        </div>
        <div class="auth-field">
          <label for="model-coding">
            Coding Agent 模型
            <span class="model-optional">可选</span>
          </label>
          <input
            id="model-coding"
            v-model="codingModel"
            autocomplete="off"
            autocapitalize="none"
            :spellcheck="false"
            maxlength="200"
            placeholder="留空使用普通聊天模型"
            :disabled="locked || customUnavailable"
            aria-describedby="model-coding-hint"
          />
          <small id="model-coding-hint">
            使用同一个 Base URL 和 API Key，模型需支持 function / tool calling。
          </small>
        </div>
        <div class="auth-field">
          <label for="model-vision">
            图片理解模型
            <span class="model-optional">可选</span>
          </label>
          <input
            id="model-vision"
            v-model="visionModel"
            autocomplete="off"
            autocapitalize="none"
            :spellcheck="false"
            maxlength="200"
            placeholder="留空则自定义模式不支持图片"
            :disabled="locked || customUnavailable"
            aria-describedby="model-vision-hint"
          />
          <small id="model-vision-hint">
            使用同一个地址与密钥；支持图片时可以填写相同模型 ID。不会自动切回默认图片模型。
          </small>
        </div>
        <p class="model-privacy">
          启用后，后续请求所需的聊天历史、附件内容及编程代码会发送到你填写的服务。请只使用可信平台，并确认有权提交这些内容。
        </p>
        <section class="model-test" aria-labelledby="model-test-heading">
          <div class="model-test-heading">
            <h3 id="model-test-heading">测试连接</h3>
            <span>使用当前表单 · 不自动保存</span>
          </div>
          <p class="account-hint">
            每次点击会发送一条简短测试请求，可能产生少量费用，计入个人 API
            调用统计但不扣默认模型额度；不会发送聊天历史、附件或本地代码。 Coding
            测试仅检查工具调用格式，不执行工具或修改文件。
          </p>
          <div class="model-test-actions">
            <button
              type="button"
              class="model-secondary"
              :disabled="locked || customUnavailable"
              @click="testConnection('chat')"
            >
              {{ busy === 'test-chat' ? '正在测试聊天…' : '测试聊天' }}
            </button>
            <button
              type="button"
              class="model-secondary"
              :disabled="locked || customUnavailable"
              @click="testConnection('coding')"
            >
              {{ busy === 'test-coding' ? '正在测试 Coding…' : '测试 Coding' }}
            </button>
          </div>
          <p v-if="testing" class="account-hint" role="status">
            正在等待模型响应，请稍候；此操作不会保存或切换当前使用的模型。
          </p>
          <div
            v-else-if="testResult"
            class="model-test-result"
            :class="`model-test-result-${testResult.state}`"
            role="status"
            aria-live="polite"
          >
            <strong>
              {{
                testResult.state === 'success'
                  ? '测试通过'
                  : testResult.state === 'unknown'
                    ? '测试结果未确认'
                    : '测试未通过'
              }}
            </strong>
            <p>{{ testResult.message }}</p>
            <dl>
              <div>
                <dt>测试类型</dt>
                <dd>{{ testResult.target === 'coding' ? 'Coding 工具调用' : '普通聊天' }}</dd>
              </div>
              <div>
                <dt>模型</dt>
                <dd>{{ testResult.model }}</dd>
              </div>
              <div>
                <dt>耗时</dt>
                <dd>{{ formatDuration(testResult.elapsedMs) }}</dd>
              </div>
              <div v-if="testResult.inputTokens != null || testResult.outputTokens != null">
                <dt>Token 用量</dt>
                <dd>
                  输入 {{ testResult.inputTokens ?? '未提供' }} / 输出
                  {{ testResult.outputTokens ?? '未提供' }}
                </dd>
              </div>
              <div v-if="testResult.errorCode">
                <dt>错误代码</dt>
                <dd>{{ testResult.errorCode }}</dd>
              </div>
              <div v-if="testResult.requestId">
                <dt>排查编号</dt>
                <dd>{{ testResult.requestId }}</dd>
              </div>
            </dl>
          </div>
          <p class="account-hint">
            测试通过只代表本次聊天或工具格式探测成功，不代表图片和所有编程任务均可用。要应用修改，仍需点击下方“保存并使用我的模型”。
          </p>
        </section>
      </template>

      <p class="account-hint">
        RAG 的 Embedding 仍使用项目原配置，已有向量库不会改变。个人 API
        不扣默认模型额度，仍受请求频率、并发和超时限制；资料向量调用使用服务器资源，单独限额。
      </p>
      <button
        class="auth-submit"
        type="submit"
        :disabled="locked || (source === 'custom' && customUnavailable)"
      >
        {{
          busy === 'save'
            ? '正在保存…'
            : source === 'custom'
              ? '保存并使用我的模型'
              : '保存并使用默认模型'
        }}
      </button>

      <div v-if="metadata.has_api_key" class="model-remove">
        <template v-if="confirmDelete">
          <p>删除后无法恢复已保存的密钥，后续请求将使用项目默认模型。确定删除吗？</p>
          <div class="model-remove-actions">
            <button type="button" class="model-danger" :disabled="locked" @click="remove">
              {{ busy === 'delete' ? '正在删除…' : '确认删除' }}
            </button>
            <button
              type="button"
              class="model-secondary"
              :disabled="locked"
              @click="setDeleteConfirmation(false)"
            >
              取消
            </button>
          </div>
        </template>
        <button
          v-else
          type="button"
          class="model-delete-link"
          :disabled="locked"
          @click="setDeleteConfirmation(true)"
        >
          删除个人配置与密钥
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.model-form {
  display: grid;
  gap: 18px;
}
.model-form .auth-field {
  margin: 0;
}
.model-form .auth-submit {
  margin-top: 0;
}
.model-storage-warning {
  display: grid;
  justify-items: start;
  gap: 10px;
}
.model-storage-warning p {
  margin: 0;
}
.model-test {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-subtle);
}
.model-test-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.model-test-heading h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}
.model-test-heading span {
  font-size: 11px;
  color: var(--muted);
}
.model-test .account-hint {
  margin: 0;
}
.model-test-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.model-test-result {
  padding: 12px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--surface);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.model-test-result-success {
  border-color: var(--accent-border);
}
.model-test-result-success > strong {
  color: var(--accent-hover);
}
.model-test-result-failure {
  border-color: var(--danger-border);
}
.model-test-result-failure > strong {
  color: var(--danger);
}
.model-test-result p {
  margin: 7px 0;
  color: var(--text-secondary);
}
.model-test-result dl {
  margin: 10px 0 0;
}
.model-test-result dl > div {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  gap: 8px;
  margin-top: 4px;
}
.model-test-result dt {
  color: var(--muted);
}
.model-test-result dd {
  margin: 0;
}
.model-source {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.model-source legend {
  margin-bottom: 12px;
  color: var(--text-secondary);
  font-size: 12px;
}
.model-source label {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 14px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
}
.model-source label.selected {
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.model-source input {
  accent-color: var(--accent);
  margin: 0;
  flex-shrink: 0;
}
.model-source input:disabled + span {
  opacity: 0.6;
}
.model-source strong {
  display: block;
  font-size: 13px;
  font-weight: 600;
}
.model-source small {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-top: 5px;
  line-height: 1.5;
}
.model-defaults {
  display: grid;
  gap: 12px;
  padding: 16px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.model-defaults > div {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
  font-size: 12px;
}
.model-defaults span {
  color: var(--muted);
}
.model-defaults strong {
  font-weight: 500;
  overflow-wrap: anywhere;
}
.model-key-saved {
  display: inline-block;
  margin-left: 6px;
  border-radius: 4px;
  padding: 2px 6px;
  background: var(--accent-soft);
  color: var(--accent-hover);
  font-size: 10px;
}
.model-optional {
  color: var(--muted);
  margin-left: 4px;
  font-size: 11px;
}
.model-privacy {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--accent-border);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.8;
}
.model-remove {
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.model-remove p {
  font-size: 12px;
  line-height: 1.8;
  color: var(--text-secondary);
  margin: 0 0 12px;
}
.model-remove-actions {
  display: flex;
  gap: 10px;
}
.model-secondary,
.model-danger {
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 9px 14px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
}
.model-danger {
  color: var(--danger);
  border-color: var(--danger-border);
  background: var(--danger-soft);
}
.model-delete-link {
  padding: 3px 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--danger);
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
@media (max-width: 380px) {
  .model-source {
    gap: 8px;
  }
  .model-source label {
    gap: 7px;
    padding: 12px 8px;
  }
}
</style>
