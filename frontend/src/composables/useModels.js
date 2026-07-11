import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useAuth.js'

const EMPTY_SELECTION = Object.freeze({
  providerId: '',
  modelId: '',
  reasoningEffort: 'auto',
})

export const modelProviders = ref([])
export const modelDefaults = reactive({
  version: 0,
  agent: { ...EMPTY_SELECTION },
  reviewer: { ...EMPTY_SELECTION },
})
export const modelSecurity = reactive({
  credentialsIsolated: false,
  message: '',
})
export const sessionModel = reactive({
  sessionId: '',
  version: 0,
  providerId: '',
  modelId: '',
  reasoningEffort: 'auto',
  synced: false,
  loading: false,
  saving: false,
})
export const modelConfigLoading = ref(false)
export const modelConfigSaving = ref(false)
export const modelError = ref('')

let draftDirty = false

function stringValue(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function normalizeEfforts(raw) {
  if (!Array.isArray(raw)) return []
  return [...new Set(raw.map(stringValue).filter((value) => value && value !== 'auto'))]
}

function normalizeModel(raw = {}) {
  const id = stringValue(raw.id || raw.model_id)
  return {
    id,
    label: stringValue(raw.label || raw.name) || id,
    enabled: raw.enabled !== false,
    supportedEfforts: normalizeEfforts(
      raw.supported_efforts ?? raw.supportedEfforts,
    ),
    supportsTemperature: Boolean(
      raw.supports_temperature ?? raw.supportsTemperature,
    ),
  }
}

function normalizeProvider(raw = {}) {
  return {
    id: stringValue(raw.id),
    name: stringValue(raw.name) || stringValue(raw.id) || '未命名提供商',
    adapter: stringValue(raw.adapter) || 'openai_compatible',
    baseUrl: stringValue(raw.base_url ?? raw.baseUrl),
    allowInsecureHttp: Boolean(
      raw.allow_insecure_http ?? raw.allowInsecureHttp,
    ),
    enabled: raw.enabled !== false,
    readOnly: Boolean(raw.read_only ?? raw.readOnly),
    apiKeyConfigured: Boolean(
      raw.api_key_configured ?? raw.apiKeyConfigured,
    ),
    models: Array.isArray(raw.models)
      ? raw.models.map(normalizeModel).filter((model) => model.id)
      : [],
    lastTestedAt: raw.last_tested_at ?? raw.lastTestedAt ?? null,
    lastTestOk: raw.last_test_ok ?? raw.lastTestOk ?? null,
    version: Number(raw.version ?? raw.revision) || 0,
  }
}

export function normalizeModelSelection(raw = {}) {
  return {
    providerId: stringValue(raw.provider_id ?? raw.providerId),
    modelId: stringValue(raw.model_id ?? raw.modelId ?? raw.model),
    reasoningEffort: stringValue(
      raw.reasoning_effort ?? raw.reasoningEffort,
    ) || 'auto',
  }
}

function responseSelection(body = {}) {
  return body.model_context || body.session_model || body.model || body.context || body
}

function readJson(response, fallback = {}) {
  return response.json().catch(() => fallback)
}

function detailMessage(body, fallback) {
  if (typeof body?.detail === 'string') return body.detail
  if (typeof body?.detail?.message === 'string') return body.detail.message
  if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === 'string') {
    return body.detail[0].msg
  }
  if (typeof body?.message === 'string') return body.message
  return fallback
}

export const availableModelGroups = computed(() => modelProviders.value
  .filter((provider) => provider.enabled && provider.apiKeyConfigured)
  .map((provider) => ({
    ...provider,
    models: provider.models.filter((model) => model.enabled),
  }))
  .filter((provider) => provider.models.length))

export const availableModels = computed(() => availableModelGroups.value.flatMap(
  (provider) => provider.models.map((model) => ({
    ...model,
    providerId: provider.id,
    providerName: provider.name,
  })),
))

export function findModel(providerId, modelId) {
  const provider = modelProviders.value.find((entry) => entry.id === providerId)
  const model = provider?.models.find((entry) => entry.id === modelId)
  return provider && model ? { provider, model } : null
}

function firstAvailableSelection() {
  const first = availableModels.value[0]
  return first ? {
    providerId: first.providerId,
    modelId: first.id,
    reasoningEffort: 'auto',
  } : { ...EMPTY_SELECTION }
}

export function validateModelSelection(raw, { fallback = true } = {}) {
  const selection = normalizeModelSelection(raw)
  const match = findModel(selection.providerId, selection.modelId)
  if (!match) return fallback ? firstAvailableSelection() : selection
  if (selection.reasoningEffort !== 'auto'
      && !match.model.supportedEfforts.includes(selection.reasoningEffort)) {
    selection.reasoningEffort = 'auto'
  }
  return selection
}

function assignSelection(target, selection) {
  const normalized = normalizeModelSelection(selection)
  target.providerId = normalized.providerId
  target.modelId = normalized.modelId
  target.reasoningEffort = normalized.reasoningEffort
}

function applyConfig(raw = {}) {
  if (Array.isArray(raw.providers)) {
    modelProviders.value = raw.providers
      .map(normalizeProvider).filter((provider) => provider.id)
  }
  if (raw.defaults && typeof raw.defaults === 'object') {
    const defaults = raw.defaults
    modelDefaults.version = Number(defaults.version) || 0
    assignSelection(modelDefaults.agent, validateModelSelection(defaults.agent || {}))
    assignSelection(modelDefaults.reviewer, validateModelSelection(
      defaults.reviewer || defaults.agent || {},
    ))
  }
  if (raw.security && typeof raw.security === 'object') {
    const security = raw.security
    modelSecurity.credentialsIsolated = Boolean(
      security.credentials_isolated ?? security.credentialsIsolated,
    )
    modelSecurity.message = stringValue(security.message)
  }
  if (!sessionModel.sessionId && !draftDirty) {
    assignSelection(sessionModel, modelDefaults.agent)
  }
  return raw
}

export async function loadModelConfig() {
  modelConfigLoading.value = true
  modelError.value = ''
  try {
    const response = await apiFetch('/api/llm/config')
    const body = await readJson(response)
    if (!response.ok) {
      throw new Error(detailMessage(
        body, `模型配置读取失败（HTTP ${response.status}）`,
      ))
    }
    applyConfig(body)
    return body
  } catch (error) {
    modelError.value = error.message || '模型配置读取失败'
    throw error
  } finally {
    modelConfigLoading.value = false
  }
}

export function beginNewModelSession() {
  sessionModel.sessionId = ''
  sessionModel.version = 0
  sessionModel.synced = false
  sessionModel.loading = false
  sessionModel.saving = false
  draftDirty = false
  assignSelection(sessionModel, validateModelSelection(modelDefaults.agent))
}

export function bindModelSession(sessionId, raw = null) {
  sessionModel.sessionId = stringValue(sessionId)
  if (raw && typeof raw === 'object') {
    applySessionModel(raw)
  } else {
    sessionModel.synced = false
  }
}

export function applySessionModel(raw = {}) {
  const source = responseSelection(raw)
  const normalized = normalizeModelSelection(source)
  if (normalized.providerId && normalized.modelId) {
    assignSelection(sessionModel, normalized)
  }
  const version = Number(source.version ?? raw.version)
  if (Number.isFinite(version) && version >= 0) sessionModel.version = version
  if (source.session_id || raw.session_id) {
    sessionModel.sessionId = stringValue(source.session_id || raw.session_id)
  }
  sessionModel.synced = true
  draftDirty = false
  return modelSelectionSnapshot()
}

export function setDraftModel(raw) {
  if (sessionModel.sessionId) {
    throw new Error('任务已经创建，请通过会话模型接口切换')
  }
  const next = validateModelSelection(raw, { fallback: false })
  const match = findModel(next.providerId, next.modelId)
  if (!match || !match.provider.enabled || !match.model.enabled) {
    throw new Error('所选模型当前不可用')
  }
  assignSelection(sessionModel, next)
  draftDirty = true
  return modelSelectionSnapshot()
}

export function modelRequestPayload() {
  if (!sessionModel.providerId || !sessionModel.modelId) return {}
  return {
    provider_id: sessionModel.providerId,
    model_id: sessionModel.modelId,
    reasoning_effort: sessionModel.reasoningEffort || 'auto',
  }
}

export function modelSelectionSnapshot(raw = sessionModel) {
  const selection = normalizeModelSelection(raw)
  const match = findModel(selection.providerId, selection.modelId)
  return {
    ...selection,
    providerName: stringValue(raw.provider_name ?? raw.providerName)
      || match?.provider.name || selection.providerId,
    modelLabel: stringValue(raw.model_label ?? raw.modelLabel)
      || match?.model.label || selection.modelId,
  }
}

export function effortLabel(value) {
  return ({
    auto: '自动', none: '关闭', minimal: '最少', low: '低', medium: '中',
    high: '高', xhigh: '极高', max: '最大',
  })[value] || value || '自动'
}

export async function loadSessionModel(sessionId = sessionModel.sessionId) {
  const id = stringValue(sessionId)
  if (!id) return { supported: false, reason: 'draft' }
  sessionModel.loading = true
  modelError.value = ''
  try {
    const response = await apiFetch(`/api/sessions/${encodeURIComponent(id)}/model`)
    if ([404, 405, 501].includes(response.status)) {
      sessionModel.synced = false
      return { supported: false, reason: 'legacy_backend' }
    }
    const body = await readJson(response)
    if (!response.ok) {
      throw new Error(detailMessage(
        body, `会话模型读取失败（HTTP ${response.status}）`,
      ))
    }
    sessionModel.sessionId = id
    applySessionModel(body)
    return { supported: true, body }
  } catch (error) {
    modelError.value = error.message || '会话模型读取失败'
    throw error
  } finally {
    sessionModel.loading = false
  }
}

export async function setActiveModel(raw) {
  const next = validateModelSelection(raw, { fallback: false })
  const match = findModel(next.providerId, next.modelId)
  if (!match || !match.provider.enabled || !match.model.enabled) {
    throw new Error('所选模型当前不可用')
  }
  if (!sessionModel.sessionId) return setDraftModel(next)
  if (sessionModel.saving) return modelSelectionSnapshot()

  sessionModel.saving = true
  modelError.value = ''
  try {
    const response = await apiFetch(
      `/api/sessions/${encodeURIComponent(sessionModel.sessionId)}/model`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: sessionModel.version,
          provider_id: next.providerId,
          model_id: next.modelId,
          reasoning_effort: next.reasoningEffort,
        }),
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      if (response.status === 409) {
        await loadSessionModel(sessionModel.sessionId).catch(() => {})
      }
      throw new Error(detailMessage(
        body, `会话模型修改失败（HTTP ${response.status}）`,
      ))
    }
    applySessionModel(body)
    return modelSelectionSnapshot()
  } catch (error) {
    modelError.value = error.message || '会话模型修改失败'
    throw error
  } finally {
    sessionModel.saving = false
  }
}

export async function updateModelDefaults({ agent, reviewer }) {
  modelConfigSaving.value = true
  modelError.value = ''
  try {
    const response = await apiFetch('/api/llm/defaults', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        version: modelDefaults.version,
        agent: {
          provider_id: agent.providerId,
          model_id: agent.modelId,
          reasoning_effort: agent.reasoningEffort || 'auto',
        },
        reviewer: {
          provider_id: reviewer.providerId,
          model_id: reviewer.modelId,
          reasoning_effort: reviewer.reasoningEffort || 'auto',
        },
      }),
    })
    const body = await readJson(response)
    if (!response.ok) {
      if (response.status === 409) await loadModelConfig().catch(() => {})
      throw new Error(detailMessage(
        body, `默认模型保存失败（HTTP ${response.status}）`,
      ))
    }
    if (body.providers || body.defaults) applyConfig(body)
    else await loadModelConfig()
    return body
  } catch (error) {
    modelError.value = error.message || '默认模型保存失败'
    throw error
  } finally {
    modelConfigSaving.value = false
  }
}

export function _resetModelStateForTests() {
  modelProviders.value = []
  modelDefaults.version = 0
  Object.assign(modelDefaults.agent, EMPTY_SELECTION)
  Object.assign(modelDefaults.reviewer, EMPTY_SELECTION)
  modelSecurity.credentialsIsolated = false
  modelSecurity.message = ''
  Object.assign(sessionModel, {
    sessionId: '', version: 0, providerId: '', modelId: '',
    reasoningEffort: 'auto', synced: false, loading: false, saving: false,
  })
  modelConfigLoading.value = false
  modelConfigSaving.value = false
  modelError.value = ''
  draftDirty = false
}
