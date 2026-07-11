// 会话状态与 SSE 事件聚合：把细粒度审计事件流聚合成回合制渲染项。
// 同一状态机同时服务实时流（含 assistant_delta）与历史回放（无 delta，
// 用 plan.thought / final_answer.answer 整段渲染）。
import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useAuth.js'
import {
  applySessionModel,
  beginNewModelSession,
  bindModelSession,
  loadSessionModel,
  modelRequestPayload,
  modelSelectionSnapshot,
  sessionModel,
} from './useModels.js'
import {
  applyPermissionCapabilities,
  applyPermissionEvent,
  beginNewPermissionSession,
  bindPermissionSession,
  createFullAccessDraftSession,
  loadPermissionContext,
  permissionContext,
  permissionRequestPayload,
  resolvePermissionRequest,
  setPermissionMode,
} from './usePermissions.js'

export const sessions = ref([])
export const activeId = ref('')
export const items = ref([])
export const currentTurn = ref(null)
const ACTIVE_TURN_STATES = new Set(['running', 'retry_wait', 'waiting_user', 'cancelling'])
// busy 描述 SSE 生命周期；业务终态可能先于 done 到达，此时仍不能开启新回合。
export const running = computed(() => currentTurn.value?.busy === true)
export const activities = computed(() => currentTurn.value?.activities || [])
// 兼容仍依赖 phase 的外围代码；新的展示直接消费 currentTurn/activities。
export const phase = computed(() => currentTurn.value?.stage
  ? { name: currentTurn.value.stage }
  : null)

let stepsById = {}
let confirmsById = {}
let activitiesById = {}
let streamingItem = null // 当前正在流式累积的 assistant 文本项
let activeController = null
let _nextIsReport = false // 下一条 final_answer 标记为报告
let _loadRequest = 0
let fullAccessDraftPromise = null

const PLANNING_ACTIVITIES = new Set([
  'constructing_tool_call',
  'preparing_file_path',
  'generating_file_content',
])

export const stats = computed(() => {
  const s = { steps: 0, auto: 0, confirmed: 0, denied: 0 }
  for (const it of items.value) {
    if (it.kind === 'intent') {
      s.denied++
      continue
    }
    if (it.kind !== 'step') continue
    s.steps++
    if (it.status === 'denied' || it.status === 'skipped') s.denied++
    else if (it.autoAllowed) s.auto++
    else if (it.status === 'done') s.confirmed++
  }
  return s
})

function push(item) {
  const r = reactive(item)
  items.value.push(r)
  return r
}

function finishStreaming(role, text) {
  const model = currentTurn.value?.model || modelSelectionSnapshot()
  // 定稿当前流式文本项；回放模式（无流）直接新建
  if (streamingItem) {
    streamingItem.role = role
    if (text) streamingItem.text = text
    streamingItem.model = model
    streamingItem.streaming = false
    streamingItem = null
  } else if (text) {
    push({ kind: 'assistant', role, text, streaming: false, model })
  }
}

export function handleEvent(ev) {
  if (currentTurn.value) currentTurn.value.lastEventAt = Date.now()
  switch (ev.type) {
    case 'session_created':
      activeId.value = ev.session_id
      bindPermissionSession(ev.session_id)
      {
        const createdModel = ev.model_context || ev.session_model || ev.model || null
        bindModelSession(
          ev.session_id,
          createdModel,
        )
        if (!createdModel) loadSessionModel(ev.session_id).catch(() => {})
      }
      if (ev.permission || ev.permission_mode) {
        applyPermissionEvent({ ...ev, type: 'permission_context' })
      }
      loadPermissionContext(ev.session_id).catch(() => {})
      refreshSessions().catch(() => {})
      break
    case 'permission_context':
    case 'permission_changed':
    case 'permission_grant_created':
    case 'permission_revoked':
    case 'permission_grants_revoked':
      applyPermissionEvent(ev)
      break
    case 'model_context':
    case 'model_changed': {
      const agentModel = ev.agent || ev.model_context?.agent || null
      const effectiveModel = agentModel ? {
        ...agentModel,
        version: ev.session_version ?? ev.version,
        session_id: ev.session_id || activeId.value,
      } : ev
      applySessionModel(effectiveModel)
      if (currentTurn.value) {
        currentTurn.value.model = modelSelectionSnapshot(effectiveModel)
      }
      break
    }
    case 'phase':
      // 兼容旧后端事件；新后端统一发送 progress。
      // operation_id 与新事件保持一致，避免同一阶段生成两条活动记录。
      upsertActivity({ stage: ev.phase, state: 'connecting',
        operation_id: ev.phase === 'planning'
          ? `planning:${ev.round ?? 0}`
          : ev.phase === 'reviewing' && ev.step_id
            ? `reviewing:${ev.step_id}` : `phase:${ev.phase}`,
        step_id: ev.step_id })
      break
    case 'progress':
      upsertActivity(ev)
      break
    case 'snapshot':
      push({ kind: 'snapshot', snapshot: ev.snapshot,
             age: ev.collected_ago_seconds ?? 0, expanded: false })
      break
    case 'assistant_delta':
      if (currentTurn.value) currentTurn.value.transport = 'streaming'
      if (!streamingItem) {
        streamingItem = push({ kind: 'assistant', role: 'streaming',
                               text: '', streaming: true,
                               model: currentTurn.value?.model || modelSelectionSnapshot() })
      }
      streamingItem.text += ev.text
      break
    case 'plan':
      if (ev.steps.length) {
        finishStreaming('thinking', ev.thought)
        for (const s of ev.steps) {
          stepsById[s.step_id] = push({
            kind: 'step', tool: s.tool, args: s.arguments,
            purpose: s.purpose, risk: s.risk,
            status: 'queued', verification: null, output: null, error: null,
            durationMs: null, autoAllowed: false, denyReason: '',
            expanded: false, startedAt: null, failureStage: '',
          })
        }
      }
      // steps 为空时不定稿：等 final_answer 统一处理（文本即答案）
      break
    case 'verification': {
      const step = stepsById[ev.step_id]
      if (!step) break
      step.verification = { rule: ev.rule, review: ev.review, decision: ev.decision }
      if (ev.decision.action === 'deny') {
        step.status = 'denied'
        step.denyReason = ev.decision.reason
      } else if (ev.decision.action === 'auto') {
        step.status = 'ready'
        step.autoAllowed = true
      } else {
        step.status = 'waiting'
      }
      break
    }
    case 'intent_filter':
      push({ kind: 'intent', decision: ev.decision, expanded: false })
      break
    case 'capability_error': {
      const step = stepsById[ev.step_id]
      if (!step) break
      const message = ev.message || (ev.code === 'unknown_tool'
        ? '模型选择了不存在的工具，正在重新规划。'
        : '该能力在当前上下文中无法继续执行。')
      step.status = 'failed'
      step.failureStage = 'planning'
      step.error = normalizeError({
        code: ev.code || 'capability_error',
        message,
        detail: message,
        retryable: !ev.do_not_retry,
      }, message, { stage: 'planning' })
      step.expanded = true
      break
    }
    case 'confirm_request':
      confirmsById[ev.confirm_id] = push({
        kind: 'confirm', confirmId: ev.confirm_id, stepId: ev.step_id,
        step: ev.step, decision: ev.decision, operation: ev.operation || null,
        choices: ev.choices || null, hidden: false,
        timeoutSeconds: ev.timeout_seconds ?? null,
      })
      if (currentTurn.value) {
        currentTurn.value.status = 'waiting_user'
        const activity = upsertActivity({ stage: 'confirmation', state: 'waiting',
          operation_id: `confirm:${ev.confirm_id}`, step_id: ev.step_id })
        if (activity && ev.timeout_seconds != null) {
          activity.deadlineAt = Date.now() + ev.timeout_seconds * 1000
        }
      }
      break
    case 'permission_request': {
      const request = ev.request || ev.permission_request || ev
      const requestId = request.id || ev.permission_request_id || ev.request_id || ev.confirm_id
      const operation = ev.operation || {
        summary: ev.summary || ev.step?.purpose || request.capability || '执行这一步操作',
        tool: ev.step?.tool || request.capability || '',
        arguments: ev.step?.arguments || {},
        effects: ev.effects || [],
        resources: request.resource
          ? [{ kind: request.resource.startsWith?.('/') ? 'path' : 'resource',
            value: request.resource, path: request.resource.startsWith?.('/') ? request.resource : undefined }]
          : [],
        suggested_scope: request.suggested_path ? { path: request.suggested_path } : null,
      }
      confirmsById[requestId] = push({
        kind: 'confirm', confirmId: ev.confirm_id || '',
        permissionRequestId: requestId, stepId: ev.step_id,
        contextVersion: request.context_version || ev.context_version || permissionContext.version,
        step: ev.step || {
          tool: operation.tool || '',
          arguments: operation.arguments || {},
          purpose: operation.summary || ev.reason || '执行这一步操作',
          risk: ev.risk || 'medium',
        },
        decision: ev.decision || {
          action: 'confirm', risk: ev.risk || 'medium', reason: ev.reason || '',
        },
        operation,
        choices: ev.choices || null,
        requiresReauthentication: Boolean(
          request.requires_reauthentication || ev.requires_reauthentication),
        hidden: false,
        timeoutSeconds: ev.timeout_seconds ?? null,
      })
      if (currentTurn.value) {
        currentTurn.value.status = 'waiting_user'
        const activity = upsertActivity({
          stage: 'confirmation', state: 'waiting',
          operation_id: `permission:${requestId}`, step_id: ev.step_id,
        })
        if (activity && ev.timeout_seconds != null) {
          activity.deadlineAt = Date.now() + ev.timeout_seconds * 1000
        }
      }
      break
    }
    case 'permission_result': {
      applyPermissionEvent(ev)
      const requestId = ev.request?.id || ev.resolution?.request_id
        || ev.permission_request_id || ev.request_id || ev.confirm_id
      const card = confirmsById[requestId]
      if (card) card.hidden = true
      const step = stepsById[ev.step_id]
      const permissionDecision = ev.decision || ev.resolution?.decision
      const allowed = ev.allowed ?? ev.approved
        ?? !['deny', 'denied'].includes(permissionDecision)
      const timedOut = ev.timed_out || ev.status === 'expired'
      if (step) step.status = allowed ? 'ready' : timedOut ? 'timed_out' : 'skipped'
      if (currentTurn.value?.status === 'waiting_user') currentTurn.value.status = 'running'
      const activity = activitiesById[`permission:${requestId}`]
      if (activity) {
        activity.state = allowed ? 'completed' : timedOut ? 'timed_out' : 'cancelled'
        activity.updatedAt = Date.now()
      }
      if (currentTurn.value && activeId.value) {
        loadPermissionContext(activeId.value).catch(() => {})
      }
      break
    }
    case 'confirm_result': {
      const card = confirmsById[ev.confirm_id]
      if (card) card.hidden = true
      const step = stepsById[ev.step_id]
      const timedOut = ev.timed_out || ev.operator === '(超时)'
      if (step) step.status = ev.approved ? 'ready' : timedOut ? 'timed_out' : 'skipped'
      if (currentTurn.value?.status === 'waiting_user') currentTurn.value.status = 'running'
      const activity = activitiesById[`confirm:${ev.confirm_id}`]
      if (activity) {
        activity.state = ev.approved ? 'completed' : timedOut ? 'timed_out' : 'cancelled'
        activity.updatedAt = Date.now()
      }
      break
    }
    case 'execution': {
      const step = stepsById[ev.step_id]
      if (step) {
        const ok = ev.ok !== false && !ev.error
          && !['[工具调用失败]', '[执行失败]', '参数不合法']
            .some((prefix) => String(ev.output || '').trimStart().startsWith(prefix))
        step.status = ok ? 'done' : 'failed'
        step.failureStage = ok ? '' : 'executing'
        step.output = ev.output
        step.error = ev.error ? normalizeError(ev.error, '工具调用失败。', { stage: 'executing' })
          : ok ? null : normalizeError(ev.output, '工具调用失败。', { stage: 'executing' })
        step.durationMs = ev.duration_ms ?? null
        if (!ok) step.expanded = true
        const activity = activitiesById[ev.operation_id || ev.step_id]
          || Object.values(activitiesById).find((entry) => (
            entry.stage === 'executing' && entry.stepId === ev.step_id
          ))
        if (activity) {
          activity.state = ok ? 'completed' : 'failed'
          activity.elapsedMs = ev.duration_ms ?? activity.elapsedMs
          activity.error = step.error
          activity.updatedAt = Date.now()
        }
      }
      break
    }
    case 'final_answer':
      if (ev.model_context || ev.model || ev.provider_id) {
        const turnModel = ev.model_context || ev.model || ev
        if (currentTurn.value) currentTurn.value.model = modelSelectionSnapshot(turnModel)
      }
      if (currentTurn.value) currentTurn.value.terminalSeen = true
      // task_error 后端会紧接一个可审计的失败结论；错误块已承载该信息，
      // 不再重复渲染一条几乎相同的 assistant 消息。
      if (items.value[items.value.length - 1]?.kind === 'task_error') {
        items.value[items.value.length - 1].answer = ev.answer
      } else {
        finishStreaming('answer', ev.answer)
      }
      if (ev.aborted) {
        const last = items.value[items.value.length - 1]
        if (last?.kind === 'assistant') last.aborted = true
      }
      if (_nextIsReport) {
        const last = items.value[items.value.length - 1]
        if (last?.kind === 'assistant') last.isReport = true
        _nextIsReport = false
      }
      if (currentTurn.value) {
        const outcome = ev.outcome || (ev.aborted ? 'failed' : 'completed')
        const lowered = String(outcome).toLowerCase()
        const status = lowered.includes('cancel') || lowered.includes('stop')
          ? 'cancelled'
          : lowered.includes('block') || lowered.includes('deny') || lowered.includes('reject')
            ? 'blocked'
            : lowered.includes('fail') || lowered.includes('error') || ev.aborted
              ? 'failed' : 'succeeded'
        finishTurn(status, { outcome, elapsedMs: ev.elapsed_ms })
      }
      break
    case 'fatal':
    case 'task_error':
      if (currentTurn.value) currentTurn.value.terminalSeen = true
      appendTaskError(ev, ev.type === 'fatal' ? '任务未能完成。' : '请求未能完成。')
      break
    case 'task_cancelled': {
      const step = stepsById[ev.step_id]
      if (step && !['done', 'failed', 'denied', 'skipped'].includes(step.status)) {
        step.status = ev.stage === 'executing' ? 'result_unknown' : 'cancelled'
        if (step.status === 'result_unknown') {
          step.error = {
            message: '任务在执行过程中断开，操作结果需要重新核验。',
            detail: '系统没有把该步骤记录为成功。',
          }
          step.expanded = true
        }
      }
      for (const card of Object.values(confirmsById)) card.hidden = true
      break
    }
    case 'done':
      if (currentTurn.value) {
        currentTurn.value.transport = 'closed'
        if (ACTIVE_TURN_STATES.has(currentTurn.value.status)
            && !currentTurn.value.terminalSeen) {
          appendTaskError({
            stage: currentTurn.value.stage,
            error: {
              code: 'terminal_event_missing',
              message: '任务连接已结束，但没有收到明确的完成状态。',
              retryable: true,
            },
          }, '任务没有返回明确结果。')
        }
      }
      break
    case 'user_query':
      // 回放模式渲染历史用户消息；实时模式已在发送时本地插入
      if (!running.value) push({ kind: 'user', text: ev.query })
      break
  }
}

export async function refreshSessions() {
  const r = await apiFetch('/api/sessions')
  if (!r.ok) throw new Error(`任务列表读取失败（HTTP ${r.status}）`)
  const body = await r.json()
  sessions.value = body.sessions || []
  applyPermissionCapabilities(body.permission_capabilities)
  return body
}

function secureDraftSessionId() {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID().replaceAll('-', '').toLowerCase()
  }
  if (typeof cryptoApi?.getRandomValues === 'function') {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16))
    return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
  }
  throw new Error('当前浏览器无法生成安全的任务标识，请升级浏览器后重试')
}

export async function setChatPermissionMode(mode, options = {}) {
  if (mode !== 'full_access' || activeId.value) {
    return setPermissionMode(mode, options)
  }
  if (fullAccessDraftPromise) return fullAccessDraftPromise

  fullAccessDraftPromise = (async () => {
    const requestedSessionId = secureDraftSessionId()
    const draftModel = modelRequestPayload()
    const result = await createFullAccessDraftSession(
      requestedSessionId,
      {
        ...options,
        providerId: draftModel.provider_id,
        modelId: draftModel.model_id,
        reasoningEffort: draftModel.reasoning_effort,
      },
    )
    if (!result.supported) {
      throw new Error(
        '当前后端不支持在首条消息前开启完全访问；请先发送第一条消息创建任务，再开启。',
      )
    }

    // 只有服务端原子创建草稿成功后，才在同一个同步片段绑定聊天与权限状态。
    activeId.value = result.sessionId
    bindPermissionSession(result.sessionId)
    bindModelSession(
      result.sessionId,
      result.body?.model_context || result.body?.session_model || result.body?.model || null,
    )
    applyPermissionEvent({ type: 'permission_context', permission: result.permission })
    try {
      await refreshSessions()
    } catch {
      // 草稿及权限已经由服务端提交；列表刷新失败不能伪装成权限开启失败。
    }
    return result
  })()

  try {
    return await fullAccessDraftPromise
  } finally {
    fullAccessDraftPromise = null
  }
}

export function newSession() {
  if (running.value) return
  _loadRequest++
  resetSessionState()
  beginNewPermissionSession()
  beginNewModelSession()
}

function turnId() {
  return globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function startTurn(prompt) {
  const now = Date.now()
  const turn = reactive({
    id: turnId(), prompt, status: 'running', stage: 'accepting',
    transport: 'connecting', startedAt: now, endedAt: null,
    elapsedMs: null, lastEventAt: now, stopRequested: false,
    outcome: null, error: null, activities: [], terminalSeen: false,
    busy: true, model: modelSelectionSnapshot(),
  })
  currentTurn.value = turn
  activitiesById = {}
  return turn
}

function finishTurn(status, { outcome = null, elapsedMs = null, error = null } = {}) {
  const turn = currentTurn.value
  if (!turn) return
  const now = Date.now()
  turn.status = status
  turn.terminalSeen = true
  turn.outcome = outcome || status
  turn.endedAt = now
  turn.elapsedMs = elapsedMs ?? Math.max(0, now - turn.startedAt)
  if (error) turn.error = error
}

function redactSecrets(value) {
  return String(value || '')
    .replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, 'sk-[已隐藏]')
    .replace(/(authorization\s*[:=]\s*bearer\s+)[^\s,"']+/ig, '$1[已隐藏]')
    .replace(/(api[_ -]?key\s*[:=]\s*)[^\s,"']+/ig, '$1[已隐藏]')
}

function normalizeError(raw, fallback = '请求未能完成。', extra = {}) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const message = typeof raw === 'string'
    ? raw
    : source.message || source.detail || fallback
  return {
    stage: extra.stage || source.stage || currentTurn.value?.stage || 'request',
    code: source.code || source.category || extra.code || 'request_failed',
    message: redactSecrets(message),
    detail: redactSecrets(source.detail || source.message || message),
    retryable: source.retryable ?? extra.retryable ?? true,
    httpStatus: source.http_status ?? source.httpStatus ?? extra.httpStatus ?? null,
    requestId: source.request_id || source.requestId || null,
    incidentId: source.incident_id || source.incidentId || null,
  }
}

function finishStreamingInterrupted() {
  if (streamingItem) {
    streamingItem.streaming = false
    streamingItem.interrupted = true
    streamingItem = null
    return
  }
  const dangling = [...items.value].reverse().find((item) => item.streaming)
  if (dangling) {
    dangling.streaming = false
    dangling.interrupted = true
  }
}

function activityId(ev) {
  return ev.operation_id || `${ev.stage || 'request'}:${ev.step_id || 'current'}`
}

function progressCount(value) {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? Math.floor(number) : null
}

function planningActivity(value) {
  return PLANNING_ACTIVITIES.has(value) ? value : ''
}

function upsertActivity(ev) {
  const turn = currentTurn.value
  if (!turn) return null
  const id = activityId(ev)
  let activity = activitiesById[id]
  if (!activity) {
    activity = reactive({
      id, stage: ev.stage || 'request', state: ev.state || 'connecting',
      operationId: ev.operation_id || '', stepId: ev.step_id || '',
      attempt: ev.attempt ?? null, maxAttempts: ev.max_attempts ?? null,
      elapsedMs: ev.elapsed_ms ?? 0, retryInMs: ev.retry_in_ms ?? 0,
      planningActivity: planningActivity(ev.activity),
      generatedChars: progressCount(ev.generated_chars),
      generatedBytes: progressCount(ev.generated_bytes),
      error: null, startedAt: Date.now(), updatedAt: Date.now(), deadlineAt: null,
    })
    activitiesById[id] = activity
    turn.activities.push(activity)
  }
  activity.stage = ev.stage || activity.stage
  activity.state = ev.state || activity.state
  activity.operationId = ev.operation_id || activity.operationId
  activity.stepId = ev.step_id || activity.stepId
  activity.attempt = ev.attempt ?? activity.attempt
  activity.maxAttempts = ev.max_attempts ?? activity.maxAttempts
  activity.elapsedMs = ev.elapsed_ms ?? activity.elapsedMs
  activity.retryInMs = ev.retry_in_ms ?? 0
  // planning 进度只接收约定的活动枚举与计数。正文、路径和原始 JSON
  // 即便出现在事件中也不会进入响应式状态，更不会被界面意外渲染。
  const startsPlanningAttempt = activity.stage === 'planning'
    && ['connecting', 'streaming'].includes(ev.state)
    && !Object.hasOwn(ev, 'activity')
  if (startsPlanningAttempt) {
    // 同一 operation_id 的模型重试会复用活动记录。新 attempt 尚未产生
    // 工具参数时，不能沿用上一轮的“正在生成文件”及其计数。
    activity.planningActivity = ''
    activity.generatedChars = null
    activity.generatedBytes = null
  } else if (Object.hasOwn(ev, 'activity')) {
    activity.planningActivity = planningActivity(ev.activity)
  }
  if (!startsPlanningAttempt && Object.hasOwn(ev, 'generated_chars')) {
    activity.generatedChars = progressCount(ev.generated_chars)
  }
  if (!startsPlanningAttempt && Object.hasOwn(ev, 'generated_bytes')) {
    activity.generatedBytes = progressCount(ev.generated_bytes)
  }
  if (ev.error) {
    activity.error = normalizeError(ev.error, '本次尝试失败。', { stage: activity.stage })
  } else if ([
    'connecting', 'streaming', 'constructing_tool_call', 'generating_content', 'completed',
  ].includes(activity.state)) {
    // 新尝试已经开始或成功，旧的退避原因不再属于当前状态。
    activity.error = null
  }
  activity.updatedAt = Date.now()
  turn.stage = activity.stage
  turn.lastEventAt = activity.updatedAt
  if (activity.state === 'retry_wait') turn.status = 'retry_wait'
  else if (turn.status === 'retry_wait') turn.status = 'running'
  turn.transport = activity.state === 'connecting' ? 'connecting' : 'streaming'

  const step = stepsById[ev.operation_id] || stepsById[ev.step_id]
  if (step && activity.stage === 'reviewing') {
    step.status = activity.state === 'failed' ? 'failed' : 'reviewing'
    step.startedAt ||= Date.now() - (activity.elapsedMs || 0)
    if (activity.error) step.error = activity.error
  }
  if (step && activity.stage === 'executing') {
    if (activity.state === 'failed') step.status = 'failed'
    else if (activity.state === 'completed') step.status = step.status === 'failed' ? 'failed' : 'done'
    else step.status = 'running'
    step.startedAt ||= Date.now() - (activity.elapsedMs || 0)
    if (activity.state === 'completed' && step.durationMs == null) {
      step.durationMs = activity.elapsedMs
    }
    if (activity.error) step.error = activity.error
  }
  return activity
}

function appendTaskError(ev, fallback) {
  const error = normalizeError(ev.error || ev, fallback, { stage: ev.stage })
  finishStreamingInterrupted()
  finishTurn('failed', { outcome: ev.outcome || 'failed', elapsedMs: ev.elapsed_ms, error })
  const prompt = currentTurn.value?.prompt
    || [...items.value].reverse().find((item) => item.kind === 'user')?.text || ''
  const previous = items.value[items.value.length - 1]
  if (previous?.kind === 'task_error' && previous.turnId === currentTurn.value?.id) {
    previous.error = error
    return
  }
  push({ kind: 'task_error', error, prompt, turnId: currentTurn.value?.id || '',
         elapsedMs: ev.elapsed_ms ?? currentTurn.value?.elapsedMs ?? null })
}

function resetSessionState() {
  activeController?.abort()
  activeController = null
  activeId.value = ''
  items.value = []
  currentTurn.value = null
  stepsById = {}
  confirmsById = {}
  activitiesById = {}
  streamingItem = null
}

export async function loadSession(id) {
  if (running.value) return
  const requestId = ++_loadRequest
  resetSessionState()
  beginNewPermissionSession()
  beginNewModelSession()
  activeId.value = id
  const summary = sessions.value.find((session) => session.id === id)
  bindPermissionSession(id, { workspaceRoot: summary?.workspace_root || '' })
  const summaryModel = summary?.model_context || summary?.session_model
    || (summary?.model && typeof summary.model === 'object' ? summary.model : null)
    || (summary?.provider_id && summary?.model_id ? summary : null)
  bindModelSession(id, summaryModel)
  try {
    const r = await apiFetch(`/api/sessions/${id}/events`)
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const body = await r.json()
    if (requestId !== _loadRequest || activeId.value !== id) return
    for (const ev of body.events || []) {
      handleEvent({ type: ev.event_type, ...ev.payload })
    }
    // 历史 permission_changed 事件只用于回放；最后以服务器当前上下文校准，
    // 避免过期的 full_access 被历史事件重新点亮。
    await loadPermissionContext(id).catch(() => {})
    await loadSessionModel(id).catch(() => {})
  } catch (error) {
    if (requestId === _loadRequest && activeId.value === id) {
      push({ kind: 'fatal', error: `任务记录读取失败：${error.message}` })
    }
  }
}

export async function sendMessage(text, { onUpdate } = {}) {
  if (!text.trim() || running.value) return
  _loadRequest++
  const prompt = text.trim()
  const turn = startTurn(prompt)
  const controller = new AbortController()
  activeController = controller
  stepsById = {}
  confirmsById = {}
  streamingItem = null
  push({ kind: 'user', text: prompt, turnId: turn.id })
  let sawDone = false
  try {
    const includeDraftModel = !activeId.value || !sessionModel.synced
    const resp = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: prompt, session_id: activeId.value,
                             request_id: turn.id,
                             ...(!activeId.value ? permissionRequestPayload() : {}),
                             ...(includeDraftModel ? modelRequestPayload() : {}) }),
      signal: controller.signal,
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      const detail = typeof body.detail === 'string' ? body.detail
        : body.detail?.message
          || (Array.isArray(body.detail) ? body.detail[0]?.msg : '')
      const error = new Error(detail || `请求失败（HTTP ${resp.status}）`)
      error.httpStatus = resp.status
      throw error
    }
    turn.transport = 'streaming'
    if (!resp.body) throw new Error('服务未返回事件流。')
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(buf))) {
        const block = buf.slice(0, boundary.index)
        buf = buf.slice(boundary.index + boundary[0].length)
        const data = block.split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data)
        if (event.type === 'done') sawDone = true
        handleEvent(event)
      }
      onUpdate?.()
    }
    if (turn.stopRequested) {
      finishStreamingInterrupted()
      finishTurn('cancelled', { outcome: 'cancelled' })
      turn.transport = 'closed'
      return
    }
    if (!sawDone && !turn.stopRequested && !turn.terminalSeen) {
      const error = new Error('事件流提前结束，回复可能不完整。')
      error.code = 'stream_interrupted'
      throw error
    }
  } catch (e) {
    if (e.name === 'AbortError' || turn.stopRequested) {
      finishStreamingInterrupted()
      finishTurn('cancelled', { outcome: 'cancelled' })
      turn.transport = 'closed'
    } else if (!turn.terminalSeen) {
      turn.transport = 'broken'
      appendTaskError({
        error: { code: e.code || 'connection_interrupted', message: e.message,
          detail: e.message, retryable: true, http_status: e.httpStatus },
        stage: turn.stage, elapsed_ms: Date.now() - turn.startedAt,
      }, '与服务的连接中断。')
    }
  } finally {
    turn.busy = false
    if (turn.transport !== 'broken') turn.transport = 'closed'
    if (activeController === controller) activeController = null
    refreshSessions().catch(() => {})
  }
}

export function cancelCurrentTurn() {
  const turn = currentTurn.value
  if (!turn || !running.value) return
  turn.stopRequested = true
  turn.status = 'cancelling'
  finishStreamingInterrupted()

  for (const card of Object.values(confirmsById)) card.hidden = true
  for (const step of Object.values(stepsById)) {
    if (['queued', 'verifying', 'reviewing', 'waiting', 'ready'].includes(step.status)) {
      step.status = 'cancelled'
    } else if (step.status === 'running') {
      step.status = 'result_unknown'
      step.error = {
        message: '已停止等待该操作的结果；操作可能已经开始，结果暂时未知。',
        detail: '已经开始的系统操作不会自动回滚。',
      }
      step.expanded = true
    }
  }
  for (const activity of turn.activities) {
    if (['completed', 'failed', 'cancelled', 'timed_out', 'result_unknown'].includes(activity.state)) continue
    const step = stepsById[activity.stepId]
    activity.state = step?.status === 'result_unknown' ? 'result_unknown' : 'cancelled'
    activity.retryInMs = 0
    activity.deadlineAt = null
    activity.updatedAt = Date.now()
  }
  activeController?.abort()
}

export async function retryMessage(text, options = {}) {
  if (running.value || !text?.trim()) return
  return sendMessage(text, options)
}

export async function resolveConfirm(card, decision = 'allow_once', scope = null) {
  return resolvePermissionRequest(card, decision, scope)
}

function _buildReportPrompt() {
  const userMsgs = items.value.filter(it => it.kind === 'user').map(it => it.text)
  const steps = items.value.filter(it => it.kind === 'step')
  const intents = items.value.filter(it => it.kind === 'intent')
  const s = stats.value

  const statusLabel = { done: '已执行', denied: '已拒绝', skipped: '已跳过' }
  const stepLines = steps.map(it => {
    const st = statusLabel[it.status] || it.status
    const out = it.output ? it.output.slice(0, 300) : '（无输出）'
    return `- [${st}] **${it.tool}**：${it.purpose}（风险等级：${it.risk}）\n  执行输出：${out}`
  })

  const intentLines = intents.map(it => `- [策略拦截] ${it.decision?.reason || '（未知原因）'}`)

  return `请根据以下 KylinGuard 运维会话记录，生成一份正式的安全运维报告（Markdown 格式，适合存档和上报）。

**会话时间**：${new Date().toLocaleString('zh-CN')}
**用户指令**：${userMsgs.join(' → ') || '（无）'}

**操作步骤**（共 ${steps.length} 步）：
${stepLines.join('\n') || '（本次会话无执行步骤）'}
${intentLines.length ? `\n**策略拦截**（${intentLines.length} 次）：\n${intentLines.join('\n')}` : ''}

**安全统计**：自动放行 ${s.auto} 步，人工确认 ${s.confirmed} 步，拒绝/跳过 ${s.denied} 步

请生成包含以下章节的报告（使用中文，专业简洁）：
1. 执行摘要
2. 操作详情
3. 安全审计结果
4. 风险评估
5. 建议措施`
}

export async function generateReport({ onUpdate } = {}) {
  if (running.value || !items.value.length) return
  _nextIsReport = true
  await sendMessage(_buildReportPrompt(), { onUpdate })
}
