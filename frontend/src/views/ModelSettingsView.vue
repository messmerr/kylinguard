<template>
  <div class="kg-page models-page">
    <div class="kg-page-inner models-inner">
      <header class="page-head">
        <p class="page-description">管理 Agent 使用的 API 连接、模型与新任务默认值。</p>
        <el-button type="primary" aria-label="添加 API 提供商" @click="openProviderDialog()">
          <KgIcon name="plus" :size="15" />添加提供商
        </el-button>
      </header>

      <div
        v-if="modelSecurity.message"
        class="security-note"
        :class="{ warning: !modelSecurity.credentialsIsolated }"
        role="status"
      >
        <KgIcon :name="modelSecurity.credentialsIsolated ? 'lock' : 'warning'" :size="15" />
        <span>{{ modelSecurity.message }}</span>
      </div>

      <section class="model-section provider-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">API 提供商</h2>
            <p>密钥不会再次返回浏览器。测试只验证凭据与 /models 列表端点；具体模型和推理参数仍以任务调用为准。</p>
          </div>
          <span class="section-count">{{ modelProviders.length }} 个连接</span>
        </div>

        <el-table v-if="modelProviders.length" :data="modelProviders" class="provider-table">
          <el-table-column label="提供商" min-width="150">
            <template #default="{ row }">
              <div class="provider-cell">
                <span class="provider-state" :class="{ enabled: row.enabled }"></span>
                <span>
                  <strong>{{ row.name }}</strong>
                  <small>{{ adapterLabel(row.adapter) }}</small>
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="API 地址" min-width="220">
            <template #default="{ row }"><code class="endpoint">{{ row.baseUrl }}</code></template>
          </el-table-column>
          <el-table-column label="模型" width="82">
            <template #default="{ row }">{{ row.models.filter(model => model.enabled).length }}</template>
          </el-table-column>
          <el-table-column label="凭据" width="86">
            <template #default="{ row }">
              <span :class="row.apiKeyConfigured ? 'status-ok' : 'status-warn'">
                {{ row.apiKeyConfigured ? '已保存' : '未设置' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="连接状态" min-width="132">
            <template #default="{ row }">
              <span class="test-status" :class="testStatusClass(row)">
                {{ testStatusText(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="" width="218" align="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button text :disabled="actionBusy" @click="testProvider(row)">测试</el-button>
                <el-button text :disabled="actionBusy" @click="discoverModels(row)">读取模型</el-button>
                <el-button text :disabled="actionBusy" @click="openProviderDialog(row)">编辑</el-button>
                <el-button text type="danger" :disabled="actionBusy" @click="deleteProvider(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div v-else-if="!modelConfigLoading" class="empty-providers">
          <span class="empty-mark"><KgIcon name="model" :size="19" /></span>
          <div>
            <strong>还没有模型连接</strong>
            <p>添加一个 OpenAI Compatible API，之后可在任务输入框中切换模型。</p>
          </div>
          <el-button @click="openProviderDialog()">添加提供商</el-button>
        </div>
        <div v-else class="loading-line"><span class="kg-spinner"></span>正在读取模型配置…</div>
      </section>

      <section class="model-section defaults-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">新任务默认值</h2>
            <p>只影响之后新建的任务；已有任务继续使用各自保存的模型。</p>
          </div>
          <el-button
            type="primary"
            :loading="modelConfigSaving"
            :disabled="!defaultsValid || !defaultsChanged"
            @click="saveDefaults"
          >保存默认值</el-button>
        </div>

        <div v-if="modelOptions.length" class="default-editor">
          <div class="default-row">
            <div class="default-copy">
              <strong>Agent 模型</strong>
              <span>分析请求、规划步骤并整理最终回复。</span>
            </div>
            <el-select v-model="defaultDraft.agent.key" filterable @change="onDefaultModelChange('agent')">
              <el-option-group v-for="group in optionGroups" :key="group.id" :label="group.name">
                <el-option v-for="model in group.models" :key="model.key" :label="model.label" :value="model.key" />
              </el-option-group>
            </el-select>
            <el-select v-model="defaultDraft.agent.reasoningEffort" aria-label="Agent 推理强度">
              <el-option
                v-for="effort in defaultEfforts('agent')"
                :key="effort"
                :label="effortLabel(effort)"
                :value="effort"
              />
            </el-select>
          </div>

          <el-collapse class="reviewer-collapse">
            <el-collapse-item name="reviewer">
              <template #title>
                <span class="reviewer-title">
                  <strong>安全复核模型</strong>
                  <span>普通权限模式下复核计划；完全访问模式不会调用。</span>
                </span>
              </template>
              <div class="default-row reviewer-row">
                <div class="default-copy">
                  <strong>复核模型</strong>
                  <span>建议选择稳定、支持结构化输出的模型。</span>
                </div>
                <el-select v-model="defaultDraft.reviewer.key" filterable @change="onDefaultModelChange('reviewer')">
                  <el-option-group v-for="group in optionGroups" :key="group.id" :label="group.name">
                    <el-option v-for="model in group.models" :key="model.key" :label="model.label" :value="model.key" />
                  </el-option-group>
                </el-select>
                <el-select v-model="defaultDraft.reviewer.reasoningEffort" aria-label="复核模型推理强度">
                  <el-option
                    v-for="effort in defaultEfforts('reviewer')"
                    :key="effort"
                    :label="effortLabel(effort)"
                    :value="effort"
                  />
                </el-select>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
        <div v-else class="defaults-unavailable">添加并启用至少一个模型后，才能设置新任务默认值。</div>
      </section>
    </div>

    <el-dialog
      v-model="providerDialogOpen"
      class="provider-dialog"
      :title="providerForm.id ? '编辑 API 提供商' : '添加 API 提供商'"
      width="min(680px, calc(100vw - 28px))"
      align-center
      destroy-on-close
      @closed="clearProviderForm"
    >
      <el-form label-position="top" class="provider-form" @submit.prevent>
        <div class="form-grid">
          <el-form-item label="名称">
            <el-input v-model="providerForm.name" maxlength="80" placeholder="例如 DeepSeek" />
          </el-form-item>
          <el-form-item label="接口类型">
            <el-select v-model="providerForm.adapter" style="width: 100%" @change="onAdapterChange">
              <el-option value="openai" label="OpenAI" />
              <el-option value="deepseek" label="DeepSeek" />
              <el-option value="dashscope" label="阿里云百炼 / DashScope" />
              <el-option value="openai_compatible" label="其他 OpenAI Compatible" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="Base URL">
          <el-input v-model="providerForm.baseUrl" placeholder="https://api.example.com/v1" />
        </el-form-item>
        <div v-if="nonLocalInsecureHttp" class="insecure-http-warning" role="alert">
          <KgIcon name="warning" :size="15" />
          <div>
            <el-checkbox v-model="providerForm.allowInsecureHttp">
              允许不安全 HTTP（仅可信内网）
            </el-checkbox>
            <span>API Key 与会话内容将以明文经过网络；公网地址请使用 HTTPS。</span>
          </div>
        </div>
        <el-form-item :label="providerForm.id ? '替换 API Key' : 'API Key'">
          <el-input
            v-model="providerForm.apiKey"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="providerForm.id && providerForm.apiKeyConfigured ? '留空则保留已保存的密钥' : '输入 API Key'"
            :disabled="providerForm.clearApiKey"
            @input="providerForm.clearApiKey = false"
          />
          <span class="field-note">密钥仅随本次保存请求发送，不会显示在提供商列表或浏览器存储中。</span>
          <el-checkbox
            v-if="providerForm.id && providerForm.apiKeyConfigured"
            v-model="providerForm.clearApiKey"
            class="clear-key"
            :disabled="Boolean(providerForm.apiKey)"
          >移除已保存的 API Key</el-checkbox>
        </el-form-item>
        <el-form-item label="管理员密码" class="admin-password">
          <el-input
            v-model="providerForm.adminPassword"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="保存模型凭据前重新验证"
            @keyup.enter="saveProvider"
          />
        </el-form-item>

        <div class="models-editor">
          <div class="models-editor-head">
            <div>
              <strong>可用模型</strong>
              <span>模型发现不可用时，可以手动填写模型 ID。</span>
            </div>
            <el-button size="small" @click="addModelRow">添加模型</el-button>
          </div>
          <div v-if="providerForm.models.length" class="model-rows">
            <div v-for="(model, index) in providerForm.models" :key="model.rowKey" class="model-row">
              <el-input v-model="model.id" placeholder="模型 ID" aria-label="模型 ID" />
              <el-input v-model="model.label" placeholder="显示名称（可选）" aria-label="模型显示名称" />
              <el-select
                v-model="model.supportedEfforts"
                multiple
                collapse-tags
                collapse-tags-tooltip
                placeholder="推理档位"
                aria-label="支持的推理强度"
              >
                <el-option v-for="effort in EFFORT_VALUES" :key="effort" :label="effortLabel(effort)" :value="effort" />
              </el-select>
              <el-checkbox v-model="model.supportsTemperature" class="temperature-capability">温度</el-checkbox>
              <el-switch v-model="model.enabled" aria-label="启用模型" />
              <button type="button" class="remove-model" aria-label="移除模型" @click="removeModelRow(index)">
                <KgIcon name="close" :size="14" />
              </button>
            </div>
          </div>
          <div v-else class="no-model-rows">保存后可“读取模型”，也可以现在手动添加。</div>
        </div>

        <div class="form-enabled">
          <div>
            <strong>启用提供商</strong>
            <span>停用后不会出现在会话模型选择器中。</span>
          </div>
          <el-switch v-model="providerForm.enabled" />
        </div>
      </el-form>

      <template #footer>
        <el-button @click="providerDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="providerSaving" @click="saveProvider">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useAuth.js'
import {
  availableModelGroups,
  effortLabel,
  loadModelConfig,
  modelConfigLoading,
  modelConfigSaving,
  modelDefaults,
  modelProviders,
  modelSecurity,
  updateModelDefaults,
} from '../composables/useModels.js'

const EFFORT_VALUES = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
const ADAPTER_DEFAULT_URLS = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com',
  dashscope: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
}
const providerDialogOpen = ref(false)
const providerSaving = ref(false)
const actionBusy = ref(false)
let rowCounter = 0

const providerForm = reactive({
  id: '', version: 0, name: '', adapter: 'openai_compatible', baseUrl: '', originalBaseUrl: '',
  allowInsecureHttp: false,
  apiKey: '', apiKeyConfigured: false, models: [], enabled: true,
  clearApiKey: false, adminPassword: '',
})
const defaultDraft = reactive({
  agent: { key: '', reasoningEffort: 'auto' },
  reviewer: { key: '', reasoningEffort: 'auto' },
})

const modelOptions = computed(() => availableModelGroups.value.flatMap(
  (group) => group.models.map((model) => ({
    ...model,
    providerId: group.id,
    providerName: group.name,
    key: `${group.id}\u0000${model.id}`,
  })),
))
const optionGroups = computed(() => availableModelGroups.value.map((group) => ({
  id: group.id,
  name: group.name,
  models: group.models.map((model) => ({
    ...model,
    key: `${group.id}\u0000${model.id}`,
  })),
})))
const defaultsValid = computed(() => (
  Boolean(defaultDraft.agent.key) && Boolean(defaultDraft.reviewer.key)
))
const defaultsChanged = computed(() => {
  const currentAgent = `${modelDefaults.agent.providerId}\u0000${modelDefaults.agent.modelId}`
  const currentReviewer = `${modelDefaults.reviewer.providerId}\u0000${modelDefaults.reviewer.modelId}`
  return defaultDraft.agent.key !== currentAgent
    || defaultDraft.agent.reasoningEffort !== modelDefaults.agent.reasoningEffort
    || defaultDraft.reviewer.key !== currentReviewer
    || defaultDraft.reviewer.reasoningEffort !== modelDefaults.reviewer.reasoningEffort
})
const nonLocalInsecureHttp = computed(() => {
  try {
    const url = new URL(providerForm.baseUrl)
    const hostname = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
    return url.protocol === 'http:'
      && !['localhost', '127.0.0.1', '::1'].includes(hostname)
  } catch {
    return false
  }
})

watch(() => [
  modelDefaults.version,
  modelDefaults.agent.providerId,
  modelDefaults.agent.modelId,
  modelDefaults.agent.reasoningEffort,
  modelDefaults.reviewer.providerId,
  modelDefaults.reviewer.modelId,
  modelDefaults.reviewer.reasoningEffort,
].join(':'), syncDefaultDraft, { immediate: true })

onMounted(() => loadModelConfig().catch((error) => {
  ElMessage.error(error.message || '模型配置读取失败')
}))

function adapterLabel(adapter) {
  return ({
    openai: 'OpenAI', deepseek: 'DeepSeek', dashscope: 'DashScope',
    openai_compatible: 'OpenAI Compatible',
  })[adapter] || adapter
}

function onAdapterChange(adapter) {
  const current = providerForm.baseUrl.trim()
  if (!current || Object.values(ADAPTER_DEFAULT_URLS).includes(current)) {
    providerForm.baseUrl = ADAPTER_DEFAULT_URLS[adapter] || ''
  }
}

function syncDefaultDraft() {
  defaultDraft.agent.key = modelDefaults.agent.providerId && modelDefaults.agent.modelId
    ? `${modelDefaults.agent.providerId}\u0000${modelDefaults.agent.modelId}` : ''
  defaultDraft.agent.reasoningEffort = modelDefaults.agent.reasoningEffort || 'auto'
  defaultDraft.reviewer.key = modelDefaults.reviewer.providerId && modelDefaults.reviewer.modelId
    ? `${modelDefaults.reviewer.providerId}\u0000${modelDefaults.reviewer.modelId}` : ''
  defaultDraft.reviewer.reasoningEffort = modelDefaults.reviewer.reasoningEffort || 'auto'
}

function splitModelKey(key) {
  const [providerId = '', modelId = ''] = String(key || '').split('\u0000')
  return { providerId, modelId }
}

function defaultModel(kind) {
  const selection = splitModelKey(defaultDraft[kind].key)
  return modelOptions.value.find((option) => (
    option.providerId === selection.providerId && option.id === selection.modelId
  ))
}

function defaultEfforts(kind) {
  return ['auto', ...(defaultModel(kind)?.supportedEfforts || [])]
}

function onDefaultModelChange(kind) {
  if (!defaultEfforts(kind).includes(defaultDraft[kind].reasoningEffort)) {
    defaultDraft[kind].reasoningEffort = 'auto'
  }
}

async function saveDefaults() {
  const agent = { ...splitModelKey(defaultDraft.agent.key),
    reasoningEffort: defaultDraft.agent.reasoningEffort }
  const reviewer = { ...splitModelKey(defaultDraft.reviewer.key),
    reasoningEffort: defaultDraft.reviewer.reasoningEffort }
  try {
    await updateModelDefaults({ agent, reviewer })
    ElMessage.success('新任务默认模型已保存')
  } catch (error) {
    ElMessage.error(error.message || '默认模型保存失败')
  }
}

function modelFormRow(model = {}) {
  return {
    rowKey: ++rowCounter,
    id: String(model.id || ''),
    label: String(model.label || ''),
    enabled: model.enabled !== false,
    supportedEfforts: [...(model.supportedEfforts || [])],
    supportsTemperature: Boolean(model.supportsTemperature),
  }
}

function openProviderDialog(provider = null) {
  clearProviderForm()
  if (provider) {
    Object.assign(providerForm, {
      id: provider.id,
      version: provider.version,
      name: provider.name,
      adapter: provider.adapter,
      baseUrl: provider.baseUrl,
      originalBaseUrl: provider.baseUrl,
      allowInsecureHttp: provider.allowInsecureHttp,
      apiKeyConfigured: provider.apiKeyConfigured,
      clearApiKey: false,
      models: provider.models.map(modelFormRow),
      enabled: provider.enabled,
    })
  }
  providerDialogOpen.value = true
}

function clearProviderForm() {
  Object.assign(providerForm, {
    id: '', version: 0, name: '', adapter: 'openai_compatible', baseUrl: '', originalBaseUrl: '',
    allowInsecureHttp: false,
    apiKey: '', apiKeyConfigured: false, models: [], enabled: true,
    clearApiKey: false, adminPassword: '',
  })
}

function addModelRow() { providerForm.models.push(modelFormRow()) }
function removeModelRow(index) { providerForm.models.splice(index, 1) }

function providerPayload() {
  return {
    name: providerForm.name.trim(),
    adapter: providerForm.adapter,
    base_url: providerForm.baseUrl.trim(),
    allow_insecure_http: providerForm.allowInsecureHttp,
    ...(providerForm.apiKey ? { api_key: providerForm.apiKey } : {}),
    clear_api_key: providerForm.clearApiKey,
    models: providerForm.models.filter((model) => model.id.trim()).map((model) => ({
      id: model.id.trim(),
      label: model.label.trim() || model.id.trim(),
      enabled: model.enabled,
      supported_efforts: [...model.supportedEfforts],
      supports_temperature: model.supportsTemperature,
    })),
    enabled: providerForm.enabled,
    admin_password: providerForm.adminPassword,
    ...(providerForm.id ? { version: providerForm.version } : {}),
  }
}

async function responseError(response, fallback) {
  const body = await response.json().catch(() => ({}))
  const detail = typeof body.detail === 'string' ? body.detail
    : body.detail?.message || body.message
  return new Error(detail || `${fallback}（HTTP ${response.status}）`)
}

async function saveProvider() {
  if (!providerForm.name.trim() || !providerForm.baseUrl.trim()) {
    ElMessage.warning('请填写提供商名称与 Base URL')
    return
  }
  if (!providerForm.id && !providerForm.apiKey) {
    ElMessage.warning('新增提供商需要填写 API Key')
    return
  }
  if (providerForm.id && originChanged() && !providerForm.apiKey) {
    ElMessage.warning('API 地址所属站点已改变，请重新输入 API Key')
    return
  }
  if (!providerForm.adminPassword) {
    ElMessage.warning('请输入管理员密码')
    return
  }
  if (nonLocalInsecureHttp.value && !providerForm.allowInsecureHttp) {
    ElMessage.warning('此地址使用不安全 HTTP；请改用 HTTPS，或明确允许可信内网 HTTP')
    return
  }
  providerSaving.value = true
  try {
    const response = await apiFetch(
      providerForm.id
        ? `/api/llm/providers/${encodeURIComponent(providerForm.id)}`
        : '/api/llm/providers',
      {
        method: providerForm.id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(providerPayload()),
      },
    )
    if (!response.ok) throw await responseError(response, '提供商保存失败')
    providerForm.apiKey = ''
    providerForm.adminPassword = ''
    providerDialogOpen.value = false
    await loadModelConfig()
    ElMessage.success('API 提供商已保存')
  } catch (error) {
    ElMessage.error(error.message || '提供商保存失败')
  } finally {
    providerSaving.value = false
  }
}

async function askAdminPassword(title) {
  try {
    const { value } = await ElMessageBox.prompt(
      '此操作会访问或修改模型服务配置，请重新验证管理员密码。',
      title,
      {
        inputType: 'password',
        inputPlaceholder: '管理员密码',
        inputValidator: (value) => Boolean(value) || '请输入管理员密码',
        confirmButtonText: '继续',
        cancelButtonText: '取消',
      },
    )
    return String(value || '')
  } catch (error) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel') return ''
    throw error
  }
}

async function providerAction(provider, action, successText) {
  const adminPassword = await askAdminPassword(successText)
  if (!adminPassword) return
  actionBusy.value = true
  try {
    const response = await apiFetch(
      `/api/llm/providers/${encodeURIComponent(provider.id)}/${action}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          admin_password: adminPassword,
          version: provider.version,
        }),
      },
    )
    if (!response.ok) throw await responseError(response, `${successText}失败`)
    await loadModelConfig()
    ElMessage.success(successText)
  } catch (error) {
    await loadModelConfig().catch(() => {})
    ElMessage.error(error.message || `${successText}失败`)
  } finally {
    actionBusy.value = false
  }
}

function testProvider(provider) {
  return providerAction(provider, 'test', '连接测试完成')
}

function discoverModels(provider) {
  return providerAction(provider, 'discover-models', '模型列表已更新')
}

async function deleteProvider(provider) {
  const adminPassword = await askAdminPassword('删除 API 提供商')
  if (!adminPassword) return
  try {
    await ElMessageBox.confirm(
      `确定删除“${provider.name}”？使用它的默认配置或会话需要先切换模型。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  actionBusy.value = true
  try {
    const response = await apiFetch(
      `/api/llm/providers/${encodeURIComponent(provider.id)}`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          admin_password: adminPassword,
          version: provider.version,
        }),
      },
    )
    if (!response.ok) throw await responseError(response, '提供商删除失败')
    await loadModelConfig()
    ElMessage.success('API 提供商已删除')
  } catch (error) {
    ElMessage.error(error.message || '提供商删除失败')
  } finally {
    actionBusy.value = false
  }
}

function testStatusClass(provider) {
  if (provider.lastTestOk === true) return 'ok'
  if (provider.lastTestOk === false) return 'failed'
  return 'unknown'
}

function testStatusText(provider) {
  if (provider.lastTestedAt == null) return '未验证'
  const timestamp = Number(provider.lastTestedAt)
  const milliseconds = Number.isFinite(timestamp)
    ? (timestamp > 1e12 ? timestamp : timestamp * 1000)
    : Date.parse(provider.lastTestedAt)
  if (!Number.isFinite(milliseconds)) return provider.lastTestOk ? '通过' : '失败'
  const elapsed = Math.max(0, Date.now() - milliseconds)
  const ago = elapsed < 60_000 ? '刚刚'
    : elapsed < 3_600_000 ? `${Math.floor(elapsed / 60_000)} 分钟前`
      : elapsed < 86_400_000 ? `${Math.floor(elapsed / 3_600_000)} 小时前`
        : new Date(milliseconds).toLocaleDateString('zh-CN')
  return `${provider.lastTestOk ? '通过' : '失败'} · ${ago}`
}

function originChanged() {
  if (!providerForm.id) return false
  try {
    return new URL(providerForm.baseUrl).origin
      !== new URL(providerForm.originalBaseUrl).origin
  } catch {
    return providerForm.baseUrl.trim() !== providerForm.originalBaseUrl.trim()
  }
}
</script>

<style scoped>
.models-inner { width: min(100%, 1120px); }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--kg-space-6); }
.page-head :deep(.el-button) { gap: 7px; }
.page-description { margin: 0; color: var(--kg-text-tertiary); font-size: 13px; }
.security-note { min-height: 38px; display: flex; align-items: center; gap: 9px; margin-top: var(--kg-space-5); padding: 8px 11px; border: 1px solid var(--kg-success-border); border-radius: var(--kg-radius-md); background: var(--kg-success-soft); color: var(--kg-success); font-size: 12px; }
.security-note.warning { border-color: var(--kg-warning-border); background: var(--kg-warning-soft); color: var(--kg-warning); }
.security-note span { color: var(--kg-text-secondary); }
.model-section { margin-top: var(--kg-space-6); }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--kg-space-5); margin-bottom: var(--kg-space-3); }
.section-head p { margin: 3px 0 0; color: var(--kg-text-tertiary); font-size: 12px; }
.section-count { color: var(--kg-text-tertiary); font-size: 12px; white-space: nowrap; }
.provider-cell { display: flex; align-items: center; gap: 9px; }
.provider-cell > span:last-child { min-width: 0; display: grid; }
.provider-cell strong { overflow: hidden; color: var(--kg-text-primary); font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.provider-cell small { margin-top: 1px; color: var(--kg-text-tertiary); font-size: 10px; }
.provider-state { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--kg-text-disabled); }
.provider-state.enabled { background: var(--kg-success); }
.endpoint { display: block; overflow: hidden; color: var(--kg-text-secondary); font: 11px/1.45 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.status-ok { color: var(--kg-success); font-size: 11px; }
.status-warn { color: var(--kg-warning); font-size: 11px; }
.test-status { font-size: 11px; white-space: nowrap; }
.test-status.ok { color: var(--kg-success); }
.test-status.failed { color: var(--kg-danger); }
.test-status.unknown { color: var(--kg-text-tertiary); }
.row-actions { display: flex; justify-content: flex-end; gap: 0; white-space: nowrap; }
.row-actions :deep(.el-button + .el-button) { margin-left: 0; }
.empty-providers { min-height: 92px; display: flex; align-items: center; gap: 13px; padding: 15px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); background: var(--kg-bg-surface-1); }
.empty-mark { width: 38px; height: 38px; display: grid; flex: none; place-items: center; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); color: var(--kg-text-tertiary); }
.empty-providers > div { min-width: 0; flex: 1; }
.empty-providers strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 550; }
.empty-providers p { margin: 3px 0 0; color: var(--kg-text-tertiary); font-size: 12px; }
.loading-line { display: flex; align-items: center; gap: 9px; min-height: 72px; color: var(--kg-text-tertiary); font-size: 12px; }
.default-editor { border-top: 1px solid var(--kg-border-subtle); }
.default-row { min-height: 78px; display: grid; grid-template-columns: minmax(210px, 1fr) minmax(210px, 320px) 126px; align-items: center; gap: var(--kg-space-4); padding: 12px 0; border-bottom: 1px solid var(--kg-border-subtle); }
.default-copy strong { display: block; color: var(--kg-text-primary); font-size: 13px; font-weight: 550; }
.default-copy span { display: block; margin-top: 2px; color: var(--kg-text-tertiary); font-size: 11px; }
.reviewer-collapse { border-top: 0; }
.reviewer-title { display: flex; min-width: 0; align-items: baseline; gap: 9px; }
.reviewer-title strong { color: var(--kg-text-secondary); font-size: 12px; font-weight: 550; }
.reviewer-title span { overflow: hidden; color: var(--kg-text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.reviewer-row { padding-right: 1px; border-bottom: 0; }
.defaults-unavailable { min-height: 62px; display: flex; align-items: center; border-top: 1px solid var(--kg-border-subtle); color: var(--kg-text-tertiary); font-size: 12px; }
.provider-form :deep(.el-form-item) { margin-bottom: 17px; }
.provider-form :deep(.el-form-item__label) { margin-bottom: 6px; color: var(--kg-text-secondary); font-size: 12px; line-height: 18px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--kg-space-4); }
.field-note { margin-top: 5px; color: var(--kg-text-tertiary); font-size: 11px; }
.clear-key { display: flex; width: max-content; margin-top: 7px; }
.clear-key :deep(.el-checkbox__label) { color: var(--kg-text-tertiary); font-size: 11px; }
.insecure-http-warning { display: flex; align-items: flex-start; gap: 9px; margin: -5px 0 17px; padding: 9px 11px; border: 1px solid var(--kg-warning-border); border-radius: var(--kg-radius-md); background: var(--kg-warning-soft); color: var(--kg-warning); }
.insecure-http-warning > div { min-width: 0; }
.insecure-http-warning span { display: block; color: var(--kg-text-tertiary); font-size: 11px; }
.models-editor { margin: 2px 0 17px; padding: 11px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); background: var(--kg-bg-surface-1); }
.models-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.models-editor-head strong { display: block; color: var(--kg-text-secondary); font-size: 12px; font-weight: 550; }
.models-editor-head span { display: block; margin-top: 1px; color: var(--kg-text-tertiary); font-size: 11px; }
.model-rows { display: grid; gap: 7px; margin-top: 10px; }
.model-row { display: grid; grid-template-columns: minmax(110px, 1.1fr) minmax(105px, 1fr) minmax(120px, .9fr) 50px 34px 28px; align-items: center; gap: 7px; }
.temperature-capability { margin-right: 0; }
.temperature-capability :deep(.el-checkbox__label) { padding-left: 4px; color: var(--kg-text-tertiary); font-size: 10px; }
.remove-model { width: 28px; height: 28px; display: grid; padding: 0; place-items: center; border: 0; border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); cursor: pointer; }
.remove-model:hover { background: var(--kg-danger-soft); color: var(--kg-danger); }
.no-model-rows { padding: 12px 0 2px; color: var(--kg-text-tertiary); font-size: 11px; }
.form-enabled { display: flex; align-items: center; justify-content: space-between; gap: var(--kg-space-5); min-height: 54px; padding: 9px 12px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); background: var(--kg-bg-surface-1); }
.form-enabled strong { display: block; color: var(--kg-text-secondary); font-size: 12px; font-weight: 550; }
.form-enabled span { display: block; margin-top: 2px; color: var(--kg-text-tertiary); font-size: 11px; }
.admin-password { margin-top: 2px; }
:global(.provider-dialog .el-dialog__body) { max-height: calc(100vh - 180px); overflow-y: auto; }

@media (max-width: 1080px) {
  .provider-table :deep(.el-table__cell:nth-child(2)) { display: none; }
}

@media (max-width: 900px) {
  .provider-table :deep(.el-table__cell:nth-child(3)),
  .provider-table :deep(.el-table__cell:nth-child(4)) { display: none; }
}

@media (max-width: 760px) {
  .page-head { align-items: center; }
  .section-head { align-items: center; }
  .section-head p { display: none; }
  .provider-table :deep(.el-table__cell:nth-child(5)) { display: none; }
  .row-actions :deep(.el-button) { padding-right: 3px; padding-left: 3px; font-size: 11px; }
  .default-row { grid-template-columns: 1fr; gap: 8px; padding: 14px 0; }
  .form-grid { grid-template-columns: 1fr; gap: 0; }
  .model-row { grid-template-columns: 1fr 1fr 50px 28px; }
  .model-row :deep(.el-select),
  .model-row :deep(.el-switch) { display: none; }
}
</style>
