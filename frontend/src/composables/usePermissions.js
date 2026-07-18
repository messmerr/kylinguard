// Agent 权限上下文：前端只负责展示和提交选择，最终授权始终由后端判定。
// 所有接口都集中在本文件，后端协议调整时不需要改动组件。
import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useApi.js'

export const PERMISSION_MODES = Object.freeze([
  {
    value: 'read_only',
    label: '只读',
    short: '只查看，不做修改',
    description: '可以检查系统和读取文件，任何修改都不会执行。',
    tone: 'neutral',
  },
  {
    value: 'ask',
    label: '确认后执行',
    short: '修改前问我',
    description: '读取自动执行；创建、修改或控制系统前需要你的允许。',
    tone: 'safe',
  },
  {
    value: 'auto_review',
    label: '自动审核',
    short: '审核通过的可逆操作自动执行',
    description: '静态规则与独立 Reviewer 通过后，可逆操作会在自动执行范围内直接执行；高风险、破坏性、越界或审核异常的动作仍会询问。',
    tone: 'warning',
  },
  {
    value: 'full_access',
    label: '完全访问',
    short: '完整能力，不逐项确认',
    description: '可使用完整 shell、文件、网络和进程能力，不再逐项确认；仍受当前 OS 身份约束，工具子进程不会继承控制面密钥。',
    tone: 'danger',
  },
])

const MODE_ALIASES = Object.freeze({
  readonly: 'read_only',
  default: 'ask',
  confirm: 'ask',
  auto: 'auto_review',
  review: 'auto_review',
  bypass: 'full_access',
})

const DEFAULT_CONTEXT = Object.freeze({
  sessionId: '',
  mode: 'ask',
  version: 0,
  executorIdentity: '将在任务创建后确认',
  executorIdentitySource: 'unknown',
  workspaceRoot: '',
  defaultWorkspaceRoot: '',
  commandShell: '/bin/bash',
  commandMaxTimeout: 900,
  fullAccessCapabilities: ['shell', 'files', 'network', 'processes'],
  executionAccountSeparated: false,
  grantsRoot: false,
  autoReviewRoots: [],
  fullAccessAvailable: true,
  fullAccessVisible: false,
  fullAccessUnavailableReason: '',
  synced: false,
})

export const permissionContext = reactive({ ...DEFAULT_CONTEXT })
export const permissionGrants = ref([])
export const permissionLoading = ref(false)
export const permissionLoadError = ref('')
export const permissionError = ref('')

// 权限读取会跨越多个 await；任务切换后，旧请求不得再写回全局状态。
// 单调递增的 token 同时处理“换会话”和“同会话重复刷新”两类竞态。
let permissionLoadToken = 0

export const permissionMode = computed(() => permissionContext.mode)
export const permissionModeMeta = computed(() => (
  PERMISSION_MODES.find((mode) => mode.value === permissionContext.mode)
  || PERMISSION_MODES[1]
))
export const visiblePermissionModes = computed(() => PERMISSION_MODES.filter(
  (mode) => mode.value !== 'full_access'
    || permissionContext.fullAccessVisible
    || permissionContext.mode === 'full_access',
))
export const fullAccessActive = computed(() => permissionContext.mode === 'full_access')
// 自动执行范围只来自全局权限配置的 auto_review_roots。
// PermissionGrant.resource 是一次/会话动作的匹配资源，即使长得像路径，
// 也绝不能被提升为工作区根目录。
export const autoReviewRoots = computed(() => [...permissionContext.autoReviewRoots])

export function executionIdentitySourceLabel(
  source = permissionContext.executorIdentitySource,
) {
  return {
    backend_process: '后端当前 OS 身份',
    configured_exec_user: '配置的 OS 执行身份',
    legacy: '旧版 API（身份来源未说明）',
    unknown: '身份来源未知',
  }[source] || '服务端未识别的身份来源'
}

function normalizeMode(mode) {
  const normalized = MODE_ALIASES[mode] || mode
  return PERMISSION_MODES.some((item) => item.value === normalized)
    ? normalized : 'ask'
}

function normalizeTimestamp(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return value < 10_000_000_000 ? value * 1000 : value
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

function normalizePath(path) {
  const value = String(path || '').trim()
  if (!value) return ''
  if (!value.startsWith('/')) throw new Error('请输入以 / 开头的服务器绝对路径')
  const parts = []
  for (const part of value.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue
    if (part === '..') {
      if (!parts.length) throw new Error('目录不能超出服务器根路径')
      parts.pop()
    } else {
      parts.push(part)
    }
  }
  return `/${parts.join('/')}` || '/'
}

function normalizeActions(actions) {
  const value = Array.isArray(actions) ? actions : actions ? [actions] : []
  return [...new Set(value.map(String))]
}

function normalizeGrant(raw = {}) {
  const resource = raw.resource && typeof raw.resource === 'object'
    ? raw.resource : {}
  const rawResource = typeof raw.resource === 'string' ? raw.resource : ''
  const resourceKind = raw.resource_kind || raw.resourceKind || resource.kind
    || ((raw.path || resource.path || rawResource.startsWith('/')) ? 'path' : 'capability')
  const path = resourceKind === 'path'
    ? (raw.path || resource.path || rawResource) : ''
  const capability = String(raw.capability || '')
  const inferredActions = capability.includes('delete') ? ['delete']
    : capability.includes('write') || capability.includes('file') ? ['create', 'modify']
      : capability.includes('service') ? ['control'] : capability ? ['execute'] : ['create', 'modify']
  return {
    id: String(raw.id || raw.grant_id || globalThis.crypto?.randomUUID?.()
      || `grant-${Date.now()}-${Math.random().toString(16).slice(2)}`),
    resourceKind,
    path: path ? normalizePath(path) : '',
    label: raw.label || raw.note || rawResource || capability || '',
    actions: normalizeActions(raw.actions || raw.allowed_actions || inferredActions),
    recursive: raw.recursive !== false,
    lifetime: raw.lifetime || raw.scope || 'session',
    expiresAt: normalizeTimestamp(raw.expires_at || raw.expiresAt),
    revoked: Boolean(raw.revoked || raw.revoked_at),
    source: raw.source || 'server',
  }
}

function replaceGrant(raw) {
  const grant = normalizeGrant(raw)
  const index = permissionGrants.value.findIndex((item) => item.id === grant.id)
  if (index >= 0) permissionGrants.value[index] = grant
  else permissionGrants.value.push(grant)
  return grant
}

function removeGrantLocal(id) {
  permissionGrants.value = permissionGrants.value.filter(
    (grant) => String(grant.id) !== String(id),
  )
}

function applyContext(raw = {}, { synced = true } = {}) {
  permissionContext.mode = normalizeMode(raw.mode || raw.permission_mode || permissionContext.mode)
  const previousVersion = permissionContext.version
  const nextVersion = Number(raw.version ?? raw.context_version ?? previousVersion) || 0
  permissionContext.version = nextVersion
  if (previousVersion > 0 && nextVersion > 0 && previousVersion !== nextVersion) {
    // 全局权限版本变化会在后端收回所有旧版本的会话动作授权。
    permissionGrants.value = []
  }
  const rawIdentity = raw.executor_identity || raw.executor_user
    || raw.execution_identity || raw.executor
  const rawIdentitySource = raw.execution_identity_source || raw.executor_identity_source
  if (rawIdentity) {
    permissionContext.executorIdentity = rawIdentity
    permissionContext.executorIdentitySource = rawIdentitySource || 'legacy'
  } else if (rawIdentitySource) {
    permissionContext.executorIdentitySource = rawIdentitySource
  }
  if (Object.hasOwn(raw, 'workspace_root') && raw.workspace_root) {
    const workspaceRoot = normalizePath(raw.workspace_root)
    if (!raw.session_id) {
      permissionContext.defaultWorkspaceRoot = workspaceRoot
      if (!permissionContext.sessionId) permissionContext.workspaceRoot = workspaceRoot
    } else {
      permissionContext.workspaceRoot = workspaceRoot
    }
  }
  permissionContext.commandShell = raw.command_shell ?? permissionContext.commandShell
  permissionContext.commandMaxTimeout = Number(
    raw.command_max_timeout ?? permissionContext.commandMaxTimeout,
  ) || DEFAULT_CONTEXT.commandMaxTimeout
  if (Array.isArray(raw.full_access_capabilities)) {
    permissionContext.fullAccessCapabilities = [...raw.full_access_capabilities]
  }
  permissionContext.executionAccountSeparated = Boolean(
    raw.execution_account_separated
    ?? raw.control_plane_isolated
    ?? permissionContext.executionAccountSeparated,
  )
  permissionContext.grantsRoot = Boolean(
    raw.grants_root ?? permissionContext.grantsRoot,
  )
  permissionContext.fullAccessAvailable = raw.full_access_available
    ?? raw.capabilities?.full_access ?? permissionContext.fullAccessAvailable
  if (Object.hasOwn(raw, 'full_access_visible')) {
    permissionContext.fullAccessVisible = Boolean(raw.full_access_visible)
  }
  permissionContext.fullAccessUnavailableReason = raw.full_access_unavailable_reason
    ?? permissionContext.fullAccessUnavailableReason
  permissionContext.synced = synced
  if (Array.isArray(raw.grants)) permissionGrants.value = raw.grants.map(normalizeGrant)
  if (Array.isArray(raw.auto_review_roots)) {
    permissionContext.autoReviewRoots = [
      ...new Set(raw.auto_review_roots.map(normalizePath)),
    ]
  }
}

export function applyPermissionCapabilities(raw = {}) {
  if (!raw || typeof raw !== 'object') return
  if (Object.hasOwn(raw, 'workspace_root') && raw.workspace_root) {
    try {
      const defaultRoot = normalizePath(raw.workspace_root)
      permissionContext.defaultWorkspaceRoot = defaultRoot
      if (!permissionContext.sessionId) permissionContext.workspaceRoot = defaultRoot
    } catch {
      // 能力元数据损坏时沿用上次有效目录；真正创建会话仍由后端校验。
    }
  }
  const fullAccess = raw.full_access
  const metadata = {}
  const keys = [
    'full_access_available', 'full_access_unavailable_reason',
    'full_access_visible',
    'execution_identity', 'execution_identity_source', 'executor_identity',
    'executor_identity_source', 'command_shell',
    'command_max_timeout', 'full_access_capabilities',
    'execution_account_separated', 'control_plane_isolated', 'grants_root',
  ]
  for (const key of keys) {
    if (Object.hasOwn(raw, key)) metadata[key] = raw[key]
  }
  if (typeof fullAccess === 'boolean'
      && !Object.hasOwn(metadata, 'full_access_available')) {
    metadata.full_access_available = fullAccess
  } else if (fullAccess && typeof fullAccess === 'object') {
    metadata.full_access_available = fullAccess.available
      ?? metadata.full_access_available
    metadata.full_access_unavailable_reason = fullAccess.unavailable_reason
      ?? fullAccess.reason ?? metadata.full_access_unavailable_reason
  }
  applyContext(metadata, { synced: permissionContext.synced })
}

async function readJson(response, fallback = {}) {
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

function isCompatibilityMiss(response) {
  return [404, 405, 501].includes(response.status)
}

export function beginNewPermissionSession() {
  permissionLoadToken++
  permissionContext.sessionId = ''
  permissionContext.workspaceRoot = permissionContext.defaultWorkspaceRoot
  permissionLoading.value = false
  permissionLoadError.value = ''
  permissionError.value = ''
  // 审批模式和自动执行范围是全局设置；只有动作授权随会话清空。
  permissionGrants.value = []
}

export function bindPermissionSession(sessionId, { workspaceRoot = '' } = {}) {
  const nextSessionId = String(sessionId || '')
  if (permissionContext.sessionId !== nextSessionId) {
    permissionLoadToken++
    permissionLoading.value = false
  }
  permissionContext.sessionId = nextSessionId
  if (workspaceRoot) permissionContext.workspaceRoot = normalizePath(workspaceRoot)
}

export function setDraftWorkspaceRoot(path) {
  if (permissionContext.sessionId) {
    throw new Error('任务创建后工作目录不可更改；请新建任务后重新选择')
  }
  const normalized = normalizePath(path)
  if (!normalized) throw new Error('请输入服务器绝对路径')
  permissionContext.workspaceRoot = normalized
  return permissionContext.workspaceRoot
}

export function permissionRequestPayload() {
  return {
    ...(permissionContext.workspaceRoot
      ? { workspace_root: permissionContext.workspaceRoot } : {}),
  }
}

export async function loadPermissionContext(
  sessionId = permissionContext.sessionId, { signal } = {},
) {
  const id = String(sessionId || '')
  bindPermissionSession(id)
  const loadToken = ++permissionLoadToken
  const isCurrentLoad = () => (
    loadToken === permissionLoadToken && permissionContext.sessionId === id
  )
  permissionLoading.value = true
  permissionLoadError.value = ''
  permissionError.value = ''
  try {
    const response = await apiFetch(
      '/api/permissions',
      { signal },
    )
    if (!isCurrentLoad()) return { supported: false, reason: 'stale' }
    if (isCompatibilityMiss(response)) {
      permissionContext.synced = false
      return { supported: false, reason: 'legacy_backend' }
    }
    const body = await readJson(response)
    if (!isCurrentLoad()) return { supported: false, reason: 'stale' }
    if (!response.ok) throw new Error(detailMessage(body, `权限读取失败（HTTP ${response.status}）`))
    const contextBody = body.permission || body.context || body
    applyContext(contextBody)

    if (!id) {
      permissionGrants.value = []
      return { supported: true }
    }

    const grantsResponse = await apiFetch(
      `/api/sessions/${encodeURIComponent(id)}/grants`,
      { signal },
    )
    if (!isCurrentLoad()) return { supported: false, reason: 'stale' }
    if (grantsResponse.ok) {
      const grantsBody = await readJson(grantsResponse)
      if (!isCurrentLoad()) return { supported: false, reason: 'stale' }
      permissionGrants.value = (grantsBody.grants || grantsBody.items || []).map(normalizeGrant)
    } else {
      const grantsBody = await readJson(grantsResponse)
      throw new Error(detailMessage(
        grantsBody, `有效授权读取失败（HTTP ${grantsResponse.status}）`,
      ))
    }
    return { supported: true }
  } catch (error) {
    if (!isCurrentLoad()) return { supported: false, reason: 'stale' }
    permissionLoadError.value = error.message || '无法读取权限设置'
    permissionError.value = permissionLoadError.value
    throw error
  } finally {
    if (isCurrentLoad()) permissionLoading.value = false
  }
}

// 审批模式与自动执行范围通过全局接口统一更新。
async function persistPermissionMode(mode, { autoReviewRoots: roots = null } = {}) {
  const contextVersion = permissionContext.version
  let effectiveRoots = roots || autoReviewRoots.value
  if (mode === 'auto_review' && !effectiveRoots.length && permissionContext.defaultWorkspaceRoot) {
    effectiveRoots = [permissionContext.defaultWorkspaceRoot]
  }
  const response = await apiFetch(
    '/api/permissions',
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode,
        version: contextVersion,
        auto_review_roots: effectiveRoots,
      }),
    },
  )
  if (isCompatibilityMiss(response)) return { supported: false, reason: 'legacy_backend' }
  const body = await readJson(response)
  if (!response.ok) {
    throw new Error(detailMessage(body, `权限修改失败（HTTP ${response.status}）`))
  }
  applyContext(body.permission || body.context || body)
  return { supported: true, body }
}

export async function setPermissionMode(mode, options = {}) {
  const normalized = normalizeMode(mode)
  permissionError.value = ''
  if (permissionContext.version < 1) {
    await loadPermissionContext(permissionContext.sessionId)
  }
  if (normalized === 'full_access') {
    if (!permissionContext.fullAccessAvailable) {
      throw new Error(permissionContext.fullAccessUnavailableReason || '当前服务未开放完全访问')
    }
    if (!permissionContext.fullAccessVisible) {
      throw new Error('完全访问入口仍处于隐藏状态，请先在“权限与安全”中显式显示')
    }
  }
  const previous = {
    mode: permissionContext.mode,
    synced: permissionContext.synced,
  }
  try {
    const result = await persistPermissionMode(normalized, options)
    if (!result.supported) {
      throw new Error('当前后端未提供全局权限设置接口，权限未更改')
    } else {
      permissionLoadError.value = ''
    }
    return result
  } catch (error) {
    Object.assign(permissionContext, previous)
    permissionError.value = error.message || '权限修改失败'
    throw error
  }
}

export async function setFullAccessVisibility(visible) {
  permissionError.value = ''
  if (permissionContext.version < 1) {
    await loadPermissionContext(permissionContext.sessionId)
  }
  if (visible && !permissionContext.fullAccessAvailable) {
    throw new Error(permissionContext.fullAccessUnavailableReason || '当前服务未开放完全访问')
  }
  const response = await apiFetch(
    '/api/permissions/full-access-visibility',
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        visible: Boolean(visible),
        version: permissionContext.version,
      }),
    },
  )
  const body = await readJson(response)
  if (!response.ok) {
    const message = detailMessage(body, `完全访问入口更新失败（HTTP ${response.status}）`)
    permissionError.value = message
    throw new Error(message)
  }
  applyContext(body.permission || body.context || body)
  permissionLoadError.value = ''
  return { supported: true, body }
}

export async function revokeFullAccess() {
  return setPermissionMode('ask')
}

export async function addAutoReviewRoot(path) {
  const normalizedPath = normalizePath(path)
  if (normalizedPath === '/') throw new Error('不能把服务器根目录设为自动执行范围')
  if (autoReviewRoots.value.includes(normalizedPath)) {
    return { path: normalizedPath, supported: permissionContext.synced }
  }
  const nextRoots = [...autoReviewRoots.value, normalizedPath]
  const result = await setPermissionMode(permissionContext.mode, {
    autoReviewRoots: nextRoots,
  })
  return { path: normalizedPath, ...result }
}

export async function revokeAutoReviewRoot(path) {
  const normalizedPath = normalizePath(path)
  const remainingRoots = autoReviewRoots.value.filter((root) => root !== normalizedPath)
  return setPermissionMode(permissionContext.mode, {
    autoReviewRoots: remainingRoots,
  })
}

export async function revokePermissionGrant(grantOrId) {
  const id = typeof grantOrId === 'object' ? grantOrId.id : grantOrId
  if (!id) return
  if (!permissionContext.sessionId) {
    removeGrantLocal(id)
    return { supported: false }
  }
  const response = await apiFetch(
    `/api/sessions/${encodeURIComponent(permissionContext.sessionId)}/grants/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )
  if (!response.ok && !isCompatibilityMiss(response)) {
    const body = await readJson(response)
    throw new Error(detailMessage(body, `授权收回失败（HTTP ${response.status}）`))
  }
  removeGrantLocal(id)
  return { supported: response.ok }
}

export function recordLocalGrant(raw) {
  return replaceGrant({ ...raw, source: raw.source || 'decision' })
}

export async function resolvePermissionRequest(card, decision, scope = null) {
  const permissionRequestId = card.permissionRequestId || card.requestId
  if (permissionRequestId) {
    const response = await apiFetch(
      `/api/permission-requests/${encodeURIComponent(permissionRequestId)}/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          context_version: card.contextVersion || permissionContext.version,
          authorized_path: decision === 'authorize_path' ? (scope?.path || '') : '',
        }),
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw new Error(detailMessage(body, `权限处理失败（HTTP ${response.status}）`))
    }
    if (body.grant) replaceGrant(body.grant)
    if (body.permission) applyContext(body.permission)
    const authorizedPath = body.resolution?.authorized_path
    if (authorizedPath && !body.permission) {
      permissionContext.autoReviewRoots = [...new Set([
        ...autoReviewRoots.value, normalizePath(authorizedPath),
      ])]
    }
    return body
  }

  // 旧 confirm_request 兼容：额外字段会由旧 Pydantic 模型忽略。
  const response = await apiFetch('/api/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      confirm_id: card.confirmId,
      approved: decision !== 'deny',
      decision,
      scope,
    }),
  })
  const body = await readJson(response)
  if (!response.ok) throw new Error(detailMessage(body, `确认失败（HTTP ${response.status}）`))
  if (!body.ok) throw new Error('该确认请求已失效或已经处理。')

  // 后端尚未返回 grant 时仅用于当前页面展示；真正执行仍由后端确认结果决定。
  if (decision === 'allow_session') {
    const path = scope?.path
    if (path) recordLocalGrant({
      id: `decision:${card.confirmId}:${path}`,
      path,
      actions: scope.actions || ['create', 'modify'],
      lifetime: 'session',
    })
  }
  return body
}

export function applyPermissionEvent(event = {}) {
  switch (event.type) {
    case 'permission_context':
      applyContext(event.permission || event.context || event)
      break
    case 'permission_changed':
      applyContext(event.permission || event.context || {
        ...event,
        mode: event.mode || event.to_mode,
      })
      break
    case 'permission_grant_created':
      if (event.grant) replaceGrant(event.grant)
      break
    case 'permission_revoked':
      removeGrantLocal(event.grant_id || event.id)
      break
    case 'permission_grants_revoked':
      if (event.grant_id) removeGrantLocal(event.grant_id)
      else permissionGrants.value = []
      break
    case 'permission_result':
      if (event.grant) replaceGrant(event.grant)
      if (event.permission) applyContext(event.permission)
      break
  }
}

// 供 Node 测试清空模块级状态；不在产品 UI 中调用。
export function _resetPermissionStateForTests() {
  permissionLoadToken++
  Object.assign(permissionContext, DEFAULT_CONTEXT)
  permissionGrants.value = []
  permissionLoading.value = false
  permissionLoadError.value = ''
  permissionError.value = ''
}
