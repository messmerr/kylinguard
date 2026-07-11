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
    value: 'trusted_workspace',
    label: '信任目录',
    short: '文件工具可直接写指定目录',
    description: '结构化文件工具可在可信目录内直接创建和修改；删除、终端命令和目录外操作仍会询问。',
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
  workspace: 'trusted_workspace',
  trusted: 'trusted_workspace',
  bypass: 'full_access',
})

const DEFAULT_CONTEXT = Object.freeze({
  sessionId: '',
  mode: 'ask',
  version: 0,
  executorIdentity: '等待服务器返回',
  executorIdentitySource: 'unknown',
  workspaceRoot: '',
  defaultWorkspaceRoot: '',
  commandShell: '/bin/bash',
  commandMaxTimeout: 900,
  fullAccessCapabilities: ['shell', 'files', 'network', 'processes'],
  executionAccountSeparated: false,
  grantsRoot: false,
  expiresAt: null,
  trustedRoots: [],
  draftTtlSeconds: null,
  fullAccessAvailable: true,
  fullAccessUnavailableReason: '',
  fullAccessMaxTtl: 30 * 60,
  permissionDefaultTtl: 30 * 60,
  permissionMaxTtl: 12 * 3600,
  synced: false,
})

export const permissionContext = reactive({ ...DEFAULT_CONTEXT })
export const permissionGrants = ref([])
export const permissionLoading = ref(false)
export const permissionError = ref('')

export const permissionMode = computed(() => permissionContext.mode)
export const permissionModeMeta = computed(() => (
  PERMISSION_MODES.find((mode) => mode.value === permissionContext.mode)
  || PERMISSION_MODES[1]
))
export const fullAccessActive = computed(() => permissionContext.mode === 'full_access')
export const fullAccessDurationMinutes = computed(() => Math.max(
  1,
  Math.floor(Math.min(
    permissionContext.permissionDefaultTtl,
    permissionContext.fullAccessMaxTtl,
  ) / 60),
))
// 可信目录只来自 SessionPermissionContext.trusted_roots。
// PermissionGrant.resource 是一次/会话动作的匹配资源，即使长得像路径，
// 也绝不能被提升为工作区根目录。
export const trustedRoots = computed(() => [...permissionContext.trustedRoots])

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
  permissionContext.version = Number(raw.version ?? raw.context_version ?? permissionContext.version) || 0
  const rawIdentity = raw.executor_identity || raw.executor_user
    || raw.execution_identity || raw.executor
  const rawIdentitySource = raw.execution_identity_source || raw.executor_identity_source
  if (rawIdentity) {
    permissionContext.executorIdentity = rawIdentity
    permissionContext.executorIdentitySource = rawIdentitySource || 'legacy'
  } else if (rawIdentitySource) {
    permissionContext.executorIdentitySource = rawIdentitySource
  }
  permissionContext.workspaceRoot = raw.workspace_root ?? permissionContext.workspaceRoot
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
  if (Object.hasOwn(raw, 'expires_at') || Object.hasOwn(raw, 'full_access_expires_at')) {
    permissionContext.expiresAt = normalizeTimestamp(
      raw.expires_at ?? raw.full_access_expires_at,
    )
  }
  permissionContext.fullAccessAvailable = raw.full_access_available
    ?? raw.capabilities?.full_access ?? permissionContext.fullAccessAvailable
  permissionContext.fullAccessUnavailableReason = raw.full_access_unavailable_reason
    ?? permissionContext.fullAccessUnavailableReason
  permissionContext.fullAccessMaxTtl = Number(
    raw.full_access_max_ttl ?? permissionContext.fullAccessMaxTtl,
  ) || DEFAULT_CONTEXT.fullAccessMaxTtl
  permissionContext.permissionDefaultTtl = Number(
    raw.permission_default_ttl ?? permissionContext.permissionDefaultTtl,
  ) || DEFAULT_CONTEXT.permissionDefaultTtl
  permissionContext.permissionMaxTtl = Number(
    raw.permission_max_ttl ?? permissionContext.permissionMaxTtl,
  ) || DEFAULT_CONTEXT.permissionMaxTtl
  permissionContext.synced = synced
  if (Array.isArray(raw.grants)) permissionGrants.value = raw.grants.map(normalizeGrant)
  if (Array.isArray(raw.trusted_roots)) {
    permissionContext.trustedRoots = [...new Set(raw.trusted_roots.map(normalizePath))]
  }
  if (raw.expired && ['trusted_workspace', 'full_access'].includes(permissionContext.mode)) {
    permissionContext.mode = 'ask'
    permissionContext.expiresAt = null
    permissionContext.trustedRoots = []
    permissionGrants.value = []
  }
  if (permissionContext.mode === 'trusted_workspace' && permissionContext.expiresAt) {
    permissionContext.draftTtlSeconds = Math.max(
      1, Math.ceil((permissionContext.expiresAt - Date.now()) / 1000),
    )
  } else if (permissionContext.mode !== 'trusted_workspace') {
    permissionContext.draftTtlSeconds = null
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
    'full_access_max_ttl', 'permission_default_ttl', 'permission_max_ttl',
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
    metadata.full_access_max_ttl = fullAccess.max_ttl
      ?? metadata.full_access_max_ttl
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
  permissionContext.sessionId = ''
  permissionContext.mode = 'ask'
  permissionContext.version = 0
  permissionContext.expiresAt = null
  permissionContext.trustedRoots = []
  permissionContext.draftTtlSeconds = null
  permissionContext.workspaceRoot = permissionContext.defaultWorkspaceRoot
  permissionContext.synced = false
  permissionError.value = ''
  // 授权属于会话；新任务从干净的 ask 模式开始。
  permissionGrants.value = []
}

export function bindPermissionSession(sessionId, { workspaceRoot = '' } = {}) {
  permissionContext.sessionId = String(sessionId || '')
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
    permission_mode: permissionContext.mode,
    trusted_roots: permissionContext.mode === 'trusted_workspace'
      ? trustedRoots.value : [],
    ...(permissionContext.mode === 'trusted_workspace'
      && permissionContext.draftTtlSeconds
      ? { permission_ttl_seconds: permissionContext.draftTtlSeconds } : {}),
    ...(permissionContext.workspaceRoot
      ? { workspace_root: permissionContext.workspaceRoot } : {}),
  }
}

export function fullAccessRemainingMs(now = Date.now()) {
  if (!fullAccessActive.value || permissionContext.expiresAt == null) return null
  return Math.max(0, permissionContext.expiresAt - now)
}

export function expirePermissionContext(now = Date.now()) {
  if (permissionContext.expiresAt == null || permissionContext.expiresAt > now) return false
  if (!['trusted_workspace', 'full_access'].includes(permissionContext.mode)) return false
  permissionContext.mode = 'ask'
  permissionContext.expiresAt = null
  permissionContext.trustedRoots = []
  permissionContext.draftTtlSeconds = null
  permissionGrants.value = []
  return true
}

export async function loadPermissionContext(sessionId = permissionContext.sessionId) {
  bindPermissionSession(sessionId)
  if (!permissionContext.sessionId) return { supported: false, reason: 'draft' }
  permissionLoading.value = true
  permissionError.value = ''
  try {
    const response = await apiFetch(
      `/api/sessions/${encodeURIComponent(permissionContext.sessionId)}/permissions`,
    )
    if (isCompatibilityMiss(response)) {
      permissionContext.synced = false
      return { supported: false, reason: 'legacy_backend' }
    }
    const body = await readJson(response)
    if (!response.ok) throw new Error(detailMessage(body, `权限读取失败（HTTP ${response.status}）`))
    const contextBody = body.permission || body.context || body
    applyContext(contextBody)

    const grantsResponse = await apiFetch(
      `/api/sessions/${encodeURIComponent(permissionContext.sessionId)}/grants`,
    )
    if (grantsResponse.ok) {
      const grantsBody = await readJson(grantsResponse)
      permissionGrants.value = (grantsBody.grants || grantsBody.items || []).map(normalizeGrant)
    } else {
      const grantsBody = await readJson(grantsResponse)
      throw new Error(detailMessage(
        grantsBody, `有效授权读取失败（HTTP ${grantsResponse.status}）`,
      ))
    }
    return { supported: true }
  } catch (error) {
    permissionError.value = error.message || '无法读取权限设置'
    throw error
  } finally {
    permissionLoading.value = false
  }
}

export async function createFullAccessDraftSession(sessionId, {
  durationMinutes = 30, ttlSeconds = null,
  workspaceRoot = permissionContext.workspaceRoot,
  providerId = '', modelId = '', reasoningEffort = 'auto',
} = {}) {
  const requestedSessionId = String(sessionId || '')
  if (!/^[a-f0-9]{32}$/.test(requestedSessionId)) {
    throw new Error('无法生成安全的任务标识，请刷新页面后重试')
  }
  if (!permissionContext.fullAccessAvailable) {
    throw new Error(permissionContext.fullAccessUnavailableReason || '当前服务未开放完全访问')
  }
  const requestedTtl = ttlSeconds ?? durationMinutes * 60
  const effectiveTtl = Math.max(
    1, Math.min(requestedTtl, permissionContext.fullAccessMaxTtl),
  )
  const selectedWorkspaceRoot = normalizePath(
    workspaceRoot || permissionContext.defaultWorkspaceRoot,
  )
  const response = await apiFetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: requestedSessionId,
      mode: 'full_access',
      ttl_seconds: effectiveTtl,
      ...(selectedWorkspaceRoot ? { workspace_root: selectedWorkspaceRoot } : {}),
      ...(providerId && modelId ? {
        provider_id: providerId,
        model_id: modelId,
        reasoning_effort: reasoningEffort || 'auto',
      } : {}),
    }),
  })
  if (isCompatibilityMiss(response)) {
    return { supported: false, reason: 'legacy_backend' }
  }
  const body = await readJson(response)
  if (!response.ok) {
    throw new Error(detailMessage(
      body, `完全访问草稿创建失败（HTTP ${response.status}）`,
    ))
  }
  const returnedSessionId = String(body.session_id || '')
  const permission = body.permission || body.context
  if (response.status !== 201 || returnedSessionId !== requestedSessionId
      || body.draft !== true || !permission || typeof permission !== 'object') {
    throw new Error('服务端返回的完全访问草稿不完整，未绑定该任务')
  }
  return {
    supported: true,
    sessionId: returnedSessionId,
    permission,
    body,
  }
}

// full_access 的后端升级协议集中在这里。若后端改为专门的 /full-access，
// 只需替换本函数，不影响 PermissionSelector、App 或 PolicyView。
async function persistPermissionMode(mode, {
  durationMinutes = 30, ttlSeconds = null, trustedRoots: roots = null,
} = {}) {
  if (!permissionContext.sessionId) return { supported: false, reason: 'draft' }
  const requestedTtl = ttlSeconds ?? durationMinutes * 60
  const maxTtl = mode === 'full_access'
    ? permissionContext.fullAccessMaxTtl : permissionContext.permissionMaxTtl
  const effectiveTtl = Math.max(1, Math.min(requestedTtl, maxTtl))
  const response = await apiFetch(
    `/api/sessions/${encodeURIComponent(permissionContext.sessionId)}/permissions`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode,
        version: permissionContext.version,
        trusted_roots: mode === 'trusted_workspace'
          ? (roots || trustedRoots.value) : [],
        ...(['trusted_workspace', 'full_access'].includes(mode)
          ? { ttl_seconds: effectiveTtl } : {}),
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
  if (permissionContext.sessionId && permissionContext.version < 1) {
    await loadPermissionContext(permissionContext.sessionId)
  }
  if (normalized === 'full_access') {
    if (!permissionContext.fullAccessAvailable) {
      throw new Error(permissionContext.fullAccessUnavailableReason || '当前服务未开放完全访问')
    }
  }
  const previous = {
    mode: permissionContext.mode,
    expiresAt: permissionContext.expiresAt,
    synced: permissionContext.synced,
  }
  try {
    const result = await persistPermissionMode(normalized, options)
    if (!result.supported) {
      // 首条消息会把草稿模式交给后端；旧后端仅对非 full 模式做视觉兼容。
      if (normalized === 'full_access') {
        throw new Error('当前后端尚未支持完全访问，未开启该模式')
      }
      permissionContext.mode = normalized
      permissionContext.expiresAt = null
      if (normalized === 'trusted_workspace') {
        permissionContext.trustedRoots = [...new Set(
          (options.trustedRoots || trustedRoots.value).map(normalizePath),
        )]
        permissionContext.draftTtlSeconds = Math.min(
          options.ttlSeconds ?? (options.durationMinutes || 30) * 60,
          permissionContext.permissionMaxTtl,
        )
      } else {
        permissionContext.draftTtlSeconds = null
      }
      permissionContext.synced = false
    }
    return result
  } catch (error) {
    Object.assign(permissionContext, previous)
    permissionError.value = error.message || '权限修改失败'
    throw error
  }
}

export async function revokeFullAccess() {
  return setPermissionMode('ask')
}

export async function addTrustedRoot(path, { lifetime = 'session' } = {}) {
  const normalizedPath = normalizePath(path)
  if (normalizedPath === '/') throw new Error('不能把服务器根目录设为可信目录')
  if (trustedRoots.value.includes(normalizedPath)) {
    return { path: normalizedPath, supported: permissionContext.synced }
  }
  const nextRoots = [...trustedRoots.value, normalizedPath]
  const ttlSeconds = lifetime === 'extended' ? 12 * 3600 : 30 * 60
  if (!permissionContext.sessionId) {
    permissionContext.trustedRoots = nextRoots
    permissionContext.draftTtlSeconds = Math.min(
      ttlSeconds, permissionContext.permissionMaxTtl,
    )
    return { path: normalizedPath, supported: false, reason: 'draft' }
  }

  const result = await setPermissionMode('trusted_workspace', {
    trustedRoots: nextRoots,
    ttlSeconds,
  })
  return { path: normalizedPath, ...result }
}

export function remainingPermissionTtlSeconds(now = Date.now()) {
  if (!permissionContext.expiresAt) return permissionContext.draftTtlSeconds
  return Math.max(1, Math.ceil((permissionContext.expiresAt - now) / 1000))
}

export async function revokeTrustedRoot(path) {
  const normalizedPath = normalizePath(path)
  const remainingRoots = trustedRoots.value.filter((root) => root !== normalizedPath)
  if (!permissionContext.sessionId) {
    permissionContext.trustedRoots = remainingRoots
    if (!remainingRoots.length) {
      permissionContext.mode = 'ask'
      permissionContext.draftTtlSeconds = null
    }
    return { supported: false }
  }
  if (!remainingRoots.length) return setPermissionMode('ask')
  return setPermissionMode('trusted_workspace', {
    trustedRoots: remainingRoots,
    ttlSeconds: remainingPermissionTtlSeconds(),
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
          trusted_path: decision === 'trust_path' ? (scope?.path || '') : '',
          ...(decision === 'trust_path' && scope?.ttlSeconds
            ? { ttl_seconds: scope.ttlSeconds } : {}),
        }),
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw new Error(detailMessage(body, `权限处理失败（HTTP ${response.status}）`))
    }
    if (body.grant) replaceGrant(body.grant)
    if (body.permission) applyContext(body.permission)
    const trustedPath = body.resolution?.trusted_path
    if (trustedPath && !body.permission) {
      permissionContext.mode = 'trusted_workspace'
      permissionContext.trustedRoots = [...new Set([
        ...trustedRoots.value, normalizePath(trustedPath),
      ])]
    }
    if (permissionContext.sessionId) loadPermissionContext(permissionContext.sessionId).catch(() => {})
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
  if (decision === 'allow_session' || decision === 'trust_path') {
    const path = scope?.path
    if (path) recordLocalGrant({
      id: `decision:${card.confirmId}:${path}`,
      path,
      actions: scope.actions || ['create', 'modify'],
      lifetime: decision === 'trust_path' ? 'persistent' : 'session',
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
  Object.assign(permissionContext, DEFAULT_CONTEXT)
  permissionGrants.value = []
  permissionLoading.value = false
  permissionError.value = ''
}
