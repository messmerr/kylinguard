// 会话状态与 SSE 事件聚合：把细粒度审计事件流聚合成回合制渲染项。
// 同一状态机同时服务实时流（含 assistant_delta）与历史回放（无 delta，
// 用 plan.thought / final_answer.answer 整段渲染）。
import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useApi.js'
import {
  contextFilePaths,
  nodesFromMessageAndMentions,
  normalizeContextFiles,
  normalizeContextMentions,
  requestContextMentions,
  serializeEditorSnapshot,
} from '../utils/contextMention.js'
import {
  applySessionModel,
  beginNewModelSession,
  bindModelSession,
  loadSessionModel,
  modelRequestPayload,
  modelSelectionSnapshot,
} from './useModels.js'
import {
  applyPermissionCapabilities,
  applyPermissionEvent,
  beginNewPermissionSession,
  bindPermissionSession,
  loadPermissionContext,
  permissionContext,
  permissionRequestPayload,
  resolvePermissionRequest,
  setPermissionMode,
} from './usePermissions.js'

export const sessions = ref([])
const ACTIVE_TURN_STATES = new Set(['running', 'retry_wait', 'waiting_user', 'cancelling'])
const SESSION_LOAD_TIMEOUT_MS = 20_000

const contexts = reactive(new Map())
const activeContextKey = ref('')

function copyDraftNodes(value) {
  const nodes = Array.isArray(value) ? value : []
  return nodes.length
    ? nodes.map((node) => ({ ...node }))
    : [{ type: 'text', text: '' }]
}

function createContext({ sessionId = '', title = '', workspaceRoot = '', hydrated = true } = {}) {
  const key = sessionId || `draft:${turnId()}`
  return reactive({
    key,
    sessionId: String(sessionId || ''),
    title: String(title || ''),
    workspaceRoot: String(workspaceRoot || ''),
    draftNodes: [{ type: 'text', text: '' }],
    updatedAt: Date.now(),
    hydrated,
    items: [],
    currentTurn: null,
    sessionLoading: false,
    stepsById: {},
    confirmsById: {},
    activitiesById: {},
    streamingItem: null,
    controller: null,
    loadController: null,
    loadRequest: 0,
    loadPromise: null,
    nextIsReport: false,
    permissionVersion: 0,
    modelContext: null,
  })
}

function addContext(options = {}) {
  const context = createContext(options)
  contexts.set(context.key, context)
  return context
}

const initialContext = addContext()
activeContextKey.value = initialContext.key

const activeContext = computed(() => contexts.get(activeContextKey.value) || null)
export const activeChatContextKey = computed(() => activeContextKey.value)
export const chatDraftNodes = computed({
  get: () => activeContext.value?.draftNodes || [{ type: 'text', text: '' }],
  set: (value) => {
    if (activeContext.value) activeContext.value.draftNodes = copyDraftNodes(value)
  },
})
export const activeId = computed(() => activeContext.value?.sessionId || '')
export const items = computed(() => activeContext.value?.items || [])
export const currentTurn = computed(() => activeContext.value?.currentTurn || null)
export const sessionLoading = computed(() => activeContext.value?.sessionLoading === true)
// busy 描述当前会话的 SSE 生命周期；业务终态可能先于 done 到达。
export const running = computed(() => currentTurn.value?.busy === true)
export const activities = computed(() => currentTurn.value?.activities || [])
// 兼容仍依赖 phase 的外围代码；新的展示直接消费 currentTurn/activities。
export const phase = computed(() => currentTurn.value?.stage
  ? { name: currentTurn.value.stage }
  : null)

function contextIdentifier(value) {
  if (value && typeof value === 'object') return String(value.id || value.sessionId || value.key || '')
  return String(value || '')
}

function findContext(value) {
  const id = contextIdentifier(value)
  if (!id) return activeContext.value
  if (contexts.has(id)) return contexts.get(id)
  return [...contexts.values()].find((context) => context.sessionId === id) || null
}

export function setChatDraft(value, contextKey = '') {
  const context = findContext(contextKey)
  if (context) context.draftNodes = copyDraftNodes(value)
}

function contextRunning(context) {
  return context?.currentTurn?.busy === true
}

function isActiveContext(context) {
  return Boolean(context && context.key === activeContextKey.value)
}

function updateLocalSessionSummary(context) {
  if (!context?.sessionId) return
  const existing = sessions.value.find((session) => session.id === context.sessionId)
  const summary = {
    ...(existing || {}),
    id: context.sessionId,
    title: existing?.title || context.title || context.currentTurn?.prompt || '新任务',
    updated_at: existing?.updated_at || context.updatedAt / 1000,
    draft: false,
  }
  sessions.value = [summary, ...sessions.value.filter((session) => session.id !== context.sessionId)]
}

function registerContextSession(context, sessionId, modelContext = null) {
  const id = String(sessionId || '').trim()
  if (!id) return
  context.sessionId = id
  context.updatedAt = Date.now()
  if (modelContext) {
    context.modelContext = modelContext
    if (context.currentTurn) context.currentTurn.model = modelSelectionSnapshot(modelContext)
  }
  updateLocalSessionSummary(context)
  if (!isActiveContext(context)) return
  bindPermissionSession(id)
  bindModelSession(id, modelContext)
}

export const runningSessionIds = computed(() => new Set(
  [...contexts.values()]
    .filter(contextRunning)
    .map((context) => context.sessionId || context.key),
))

export function isSessionRunning(sessionId) {
  return contextRunning(findContext(sessionId))
}

export function sessionNeedsAttention(sessionId) {
  const context = findContext(sessionId)
  if (!context) return false
  return context.currentTurn?.status === 'waiting_user'
    || Object.values(context.confirmsById).some((card) => !card.hidden)
}

// 供 Node 测试清空模块级会话；不在产品 UI 中调用。
export function _resetChatStateForTests() {
  for (const context of contexts.values()) {
    context.controller?.abort()
    context.loadController?.abort()
  }
  contexts.clear()
  const context = addContext()
  activeContextKey.value = context.key
  sessions.value = []
}

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

function push(context, item) {
  const r = reactive(item)
  context.items.push(r)
  return r
}

function finishStreaming(context, role, text) {
  const model = context.currentTurn?.model || modelSelectionSnapshot()
  // 定稿当前流式文本项；回放模式（无流）直接新建
  if (context.streamingItem) {
    context.streamingItem.role = role
    if (text) context.streamingItem.text = text
    context.streamingItem.model = model
    context.streamingItem.streaming = false
    context.streamingItem = null
  } else if (text) {
    push(context, { kind: 'assistant', role, text, streaming: false, model })
  }
}

export function handleEvent(ev, targetContext = activeContext.value) {
  const context = targetContext || activeContext.value
  if (!context) return
  if (context.currentTurn) context.currentTurn.lastEventAt = Date.now()
  switch (ev.type) {
    case 'session_created':
      {
        const createdModel = ev.model_context || ev.session_model || ev.model || null
        registerContextSession(context, ev.session_id, createdModel)
      }
      if (!isActiveContext(context)) {
        refreshSessions().catch(() => {})
        break
      }
      {
        const createdModel = ev.model_context || ev.session_model || ev.model || null
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
      context.permissionVersion = Number(
        ev.context_version ?? ev.version ?? ev.permission?.version ?? context.permissionVersion,
      ) || context.permissionVersion
      if (isActiveContext(context)) applyPermissionEvent(ev)
      break
    case 'model_context':
    case 'model_changed': {
      const agentModel = ev.agent || ev.model_context?.agent || null
      const effectiveModel = agentModel ? {
        ...agentModel,
        version: ev.session_version ?? ev.version,
        session_id: ev.session_id || context.sessionId,
      } : ev
      context.modelContext = effectiveModel
      if (isActiveContext(context)) applySessionModel(effectiveModel)
      if (context.currentTurn) {
        context.currentTurn.model = modelSelectionSnapshot(effectiveModel)
      }
      break
    }
    case 'phase':
      // 兼容旧后端事件；新后端统一发送 progress。
      // operation_id 与新事件保持一致，避免同一阶段生成两条活动记录。
      upsertActivity(context, { stage: ev.phase, state: 'connecting',
        operation_id: ev.phase === 'planning'
          ? `planning:${ev.round ?? 0}`
          : ev.phase === 'reviewing' && ev.step_id
            ? `reviewing:${ev.step_id}` : `phase:${ev.phase}`,
        step_id: ev.step_id })
      break
    case 'progress':
      upsertActivity(context, ev)
      break
    case 'snapshot': {
      const ageSeconds = Math.max(0, Number(ev.collected_ago_seconds) || 0)
      const eventTime = Date.parse(ev.event_timestamp || '')
      const referenceTime = Number.isFinite(eventTime) ? eventTime : Date.now()
      push(context, {
        kind: 'snapshot', snapshot: ev.snapshot,
        collectedAt: referenceTime - ageSeconds * 1000,
        expanded: false,
      })
      break
    }
    case 'assistant_delta':
      if (context.currentTurn) context.currentTurn.transport = 'streaming'
      if (!context.streamingItem) {
        context.streamingItem = push(context, { kind: 'assistant', role: 'streaming',
                                                text: '', streaming: true,
                                                model: context.currentTurn?.model || modelSelectionSnapshot() })
      }
      context.streamingItem.text += ev.text
      break
    case 'plan':
      if (ev.steps.length) {
        finishStreaming(context, 'thinking', ev.thought)
        for (const s of ev.steps) {
          context.stepsById[s.step_id] = push(context, {
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
      const step = context.stepsById[ev.step_id]
      if (!step) break
      step.verification = {
        rule: ev.rule,
        review: ev.review,
        decision: ev.decision,
        toolRisk: ev.tool_risk || ev.toolRisk || null,
      }
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
      push(context, { kind: 'intent', decision: ev.decision, expanded: false })
      break
    case 'capability_error': {
      const step = context.stepsById[ev.step_id]
      if (!step) break
      const detail = ev.message || (ev.code === 'unknown_tool'
        ? '模型选择的工具当前不可用。'
        : '该能力在当前上下文中无法继续执行。')
      const message = ev.code === 'unknown_tool'
        ? '所选工具当前不可用，系统正在调整方案。'
        : detail
      step.status = 'failed'
      step.failureStage = 'planning'
      step.error = normalizeError({
        code: ev.code || 'capability_error',
        message,
        detail,
        retryable: !ev.do_not_retry,
      }, message, { stage: 'planning' })
      step.expanded = true
      break
    }
    case 'confirm_request':
      context.confirmsById[ev.confirm_id] = push(context, {
        kind: 'confirm', confirmId: ev.confirm_id, stepId: ev.step_id,
        step: ev.step, decision: ev.decision, operation: ev.operation || null,
        choices: ev.choices || null, hidden: false,
        timeoutSeconds: ev.timeout_seconds ?? null,
      })
      if (context.currentTurn) {
        context.currentTurn.status = 'waiting_user'
        const activity = upsertActivity(context, { stage: 'confirmation', state: 'waiting',
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
      context.confirmsById[requestId] = push(context, {
        kind: 'confirm', confirmId: ev.confirm_id || '',
        permissionRequestId: requestId, stepId: ev.step_id,
        contextVersion: request.context_version || ev.context_version
          || context.permissionVersion || permissionContext.version,
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
        hidden: false,
        timeoutSeconds: ev.timeout_seconds ?? null,
      })
      if (context.currentTurn) {
        context.currentTurn.status = 'waiting_user'
        const activity = upsertActivity(context, {
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
      context.permissionVersion = Number(
        ev.context_version ?? ev.version ?? ev.permission?.version ?? context.permissionVersion,
      ) || context.permissionVersion
      if (isActiveContext(context)) applyPermissionEvent(ev)
      const requestId = ev.request?.id || ev.resolution?.request_id
        || ev.permission_request_id || ev.request_id || ev.confirm_id
      const card = context.confirmsById[requestId]
      if (card) card.hidden = true
      const step = context.stepsById[ev.step_id]
      const permissionDecision = ev.decision || ev.resolution?.decision
      const allowed = ev.allowed ?? ev.approved
        ?? !['deny', 'denied'].includes(permissionDecision)
      const timedOut = ev.timed_out || ev.status === 'expired'
      if (step) step.status = allowed ? 'ready' : timedOut ? 'timed_out' : 'skipped'
      if (context.currentTurn?.status === 'waiting_user') context.currentTurn.status = 'running'
      const activity = context.activitiesById[`permission:${requestId}`]
      if (activity) {
        activity.state = allowed ? 'completed' : timedOut ? 'timed_out' : 'cancelled'
        activity.updatedAt = Date.now()
      }
      if (isActiveContext(context) && context.currentTurn && context.sessionId) {
        loadPermissionContext(context.sessionId).catch(() => {})
      }
      break
    }
    case 'confirm_result': {
      const card = context.confirmsById[ev.confirm_id]
      if (card) card.hidden = true
      const step = context.stepsById[ev.step_id]
      const timedOut = ev.timed_out || ev.operator === '(超时)'
      if (step) step.status = ev.approved ? 'ready' : timedOut ? 'timed_out' : 'skipped'
      if (context.currentTurn?.status === 'waiting_user') context.currentTurn.status = 'running'
      const activity = context.activitiesById[`confirm:${ev.confirm_id}`]
      if (activity) {
        activity.state = ev.approved ? 'completed' : timedOut ? 'timed_out' : 'cancelled'
        activity.updatedAt = Date.now()
      }
      break
    }
    case 'execution': {
      const step = context.stepsById[ev.step_id]
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
        const activity = context.activitiesById[ev.operation_id || ev.step_id]
          || Object.values(context.activitiesById).find((entry) => (
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
        context.modelContext = turnModel
        if (context.currentTurn) context.currentTurn.model = modelSelectionSnapshot(turnModel)
      }
      if (context.currentTurn) context.currentTurn.terminalSeen = true
      // task_error 后端会紧接一个可审计的失败结论；错误块已承载该信息，
      // 不再重复渲染一条几乎相同的 assistant 消息。
      if (context.items[context.items.length - 1]?.kind === 'task_error') {
        context.items[context.items.length - 1].answer = ev.answer
      } else {
        finishStreaming(context, 'answer', ev.answer)
      }
      if (ev.aborted) {
        const last = context.items[context.items.length - 1]
        if (last?.kind === 'assistant') last.aborted = true
      }
      if (context.nextIsReport) {
        const last = context.items[context.items.length - 1]
        if (last?.kind === 'assistant') last.isReport = true
        context.nextIsReport = false
      }
      if (context.currentTurn) {
        const outcome = ev.outcome || (ev.aborted ? 'failed' : 'completed')
        const lowered = String(outcome).toLowerCase()
        const status = lowered.includes('cancel') || lowered.includes('stop')
          ? 'cancelled'
          : lowered.includes('block') || lowered.includes('deny') || lowered.includes('reject')
            ? 'blocked'
            : lowered.includes('fail') || lowered.includes('error') || ev.aborted
              ? 'failed' : 'succeeded'
        finishTurn(context, status, { outcome, elapsedMs: ev.elapsed_ms })
      }
      break
    case 'fatal':
    case 'task_error':
      if (context.currentTurn) context.currentTurn.terminalSeen = true
      appendTaskError(context, ev, ev.type === 'fatal' ? '任务未能完成。' : '请求未能完成。')
      break
    case 'task_cancelled': {
      const step = context.stepsById[ev.step_id]
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
      for (const card of Object.values(context.confirmsById)) card.hidden = true
      break
    }
    case 'skill_selected': {
      const skillId = String(ev.id || ev.skill_id || '')
      const skillName = String(ev.name || ev.skill_name || '')
      const userItem = [...context.items].reverse().find((item) => item.kind === 'user')
      const selectedMode = ['auto', 'manual', 'none'].includes(ev.skill_mode)
        ? ev.skill_mode : context.currentTurn?.skillMode || userItem?.skillMode || 'auto'
      if (userItem) {
        userItem.skillId = skillId // 旧 UI/旧事件兼容
        userItem.skillName = skillName
        userItem.skillMode = selectedMode
        userItem.skillResolved = true
        if (selectedMode === 'auto') {
          userItem.routedSkillId = skillId
          userItem.routedSkillName = skillName
        } else if (!userItem.skillIds?.length) {
          userItem.skillIds = normalizeSkillIds(ev.skill_ids, skillId)
        }
        if (selectedMode === 'manual' && skillName) {
          userItem.skillNames ||= []
          const position = Number(ev.position)
          if (Number.isInteger(position) && position > 0) {
            userItem.skillNames[position - 1] = skillName
          } else if (!userItem.skillNames.includes(skillName)) {
            userItem.skillNames.push(skillName)
          }
        }
      }
      if (context.currentTurn) {
        context.currentTurn.skillId = skillId
        context.currentTurn.skillMode = selectedMode
        context.currentTurn.skillResolved = true
        if (selectedMode === 'auto') {
          context.currentTurn.routedSkillId = skillId
          context.currentTurn.routedSkillName = skillName
        } else if (!context.currentTurn.skillIds?.length) {
          context.currentTurn.skillIds = normalizeSkillIds(ev.skill_ids, skillId)
        }
        if (selectedMode === 'manual' && skillName) {
          context.currentTurn.skillNames ||= []
          const position = Number(ev.position)
          if (Number.isInteger(position) && position > 0) {
            context.currentTurn.skillNames[position - 1] = skillName
          } else if (!context.currentTurn.skillNames.includes(skillName)) {
            context.currentTurn.skillNames.push(skillName)
          }
        }
      }
      break
    }
    case 'skill_not_selected': {
      const userItem = [...context.items].reverse().find((item) => item.kind === 'user')
      const selectedMode = ['auto', 'none'].includes(ev.skill_mode)
        ? ev.skill_mode : context.currentTurn?.skillMode || userItem?.skillMode || 'auto'
      if (userItem) {
        userItem.skillId = ''
        userItem.skillName = ''
        userItem.skillMode = selectedMode
        userItem.skillResolved = true
        userItem.routedSkillId = ''
        userItem.routedSkillName = ''
      }
      if (context.currentTurn) {
        context.currentTurn.skillId = ''
        context.currentTurn.skillMode = selectedMode
        context.currentTurn.skillResolved = true
        context.currentTurn.routedSkillId = ''
        context.currentTurn.routedSkillName = ''
      }
      break
    }
    case 'done':
      if (context.currentTurn) {
        context.currentTurn.transport = 'closed'
        if (ACTIVE_TURN_STATES.has(context.currentTurn.status)
            && !context.currentTurn.terminalSeen) {
          appendTaskError(context, {
            stage: context.currentTurn.stage,
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
      if (!contextRunning(context)) {
        const skillMode = ['auto', 'manual', 'none'].includes(ev.skill_mode)
          ? ev.skill_mode : ''
        const skillIds = skillMode === 'manual'
          ? normalizeSkillIds(ev.requested_skill_ids ?? ev.skill_ids, ev.skill_id) : []
        const contextMentions = normalizeContextMentions(ev.context_mentions)
        const contextFiles = normalizeContextFiles(ev.context_files)
        push(context, {
          kind: 'user', text: String(ev.query || ''),
          contentNodes: nodesFromMessageAndMentions(ev.query, contextMentions),
          contextMentions,
          skillIds,
          skillId: String(ev.skill_id || ''),
          skillName: String(ev.skill_name || ''),
          skillMode,
          // UI 会逐项隐藏已放回正文的文件；未 mention 的兼容客户端引用仍保留。
          contextFiles,
          routedSkillId: skillMode === 'auto' ? String(ev.skill_id || '') : '',
          routedSkillName: skillMode === 'auto' ? String(ev.skill_name || '') : '',
          skillResolved: Boolean(ev.skill_id) || ev.skill_selected === false,
        })
      }
      break
  }
}

export async function refreshSessions() {
  const r = await apiFetch('/api/sessions')
  if (!r.ok) throw new Error(`任务列表读取失败（HTTP ${r.status}）`)
  const body = await r.json()
  const remote = (body.sessions || []).filter((session) => !session.draft)
  const remoteIds = new Set(remote.map((session) => session.id))
  const local = [...contexts.values()]
    .filter((context) => (
      context.sessionId && contextRunning(context) && !remoteIds.has(context.sessionId)
    ))
    .map((context) => ({
      id: context.sessionId,
      title: context.title || context.currentTurn?.prompt || '新任务',
      created_at: context.updatedAt / 1000,
      updated_at: context.updatedAt / 1000,
      draft: false,
      workspace_root: context.workspaceRoot || '',
      model: context.modelContext,
    }))
  sessions.value = [...remote, ...local]
  applyPermissionCapabilities(body.permission_capabilities)
  return body
}

export async function setChatPermissionMode(mode, options = {}) {
  return setPermissionMode(mode, options)
}

export function newSession() {
  abortContextLoad(activeContext.value)
  const context = addContext()
  activeContextKey.value = context.key
  beginNewPermissionSession()
  beginNewModelSession()
}

function turnId() {
  return globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function normalizeSkillIds(values, fallback = '', limit = 4) {
  const source = Array.isArray(values) ? values : []
  const result = []
  const seen = new Set()
  for (const raw of [...source, fallback]) {
    const id = String(raw || '').trim()
    if (!id || seen.has(id)) continue
    seen.add(id)
    result.push(id)
    if (result.length >= limit) break
  }
  return result
}

function startTurn(context, prompt, {
  skillIds = [], skillMode = 'auto', contextFiles = [], contextMentions = [], contentNodes = [],
} = {}) {
  const now = Date.now()
  const turn = reactive({
    id: turnId(), prompt, status: 'running', stage: 'accepting',
    transport: 'connecting', startedAt: now, endedAt: null,
    elapsedMs: null, lastEventAt: now, stopRequested: false,
    outcome: null, error: null, activities: [], terminalSeen: false,
    busy: true, model: modelSelectionSnapshot(),
    skillIds, skillId: skillIds[0] || '', skillMode, contextFiles, contextMentions, contentNodes,
    routedSkillId: '', routedSkillName: '', skillResolved: false,
  })
  context.currentTurn = turn
  context.activitiesById = {}
  context.modelContext = turn.model
  context.permissionVersion = permissionContext.version
  context.workspaceRoot = permissionContext.workspaceRoot || context.workspaceRoot
  context.title ||= prompt
  context.updatedAt = now
  return turn
}

function finishTurn(context, status, { outcome = null, elapsedMs = null, error = null } = {}) {
  const turn = context.currentTurn
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

function normalizeError(raw, fallback = '请求未能完成。', extra = {}, context = activeContext.value) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const message = typeof raw === 'string'
    ? raw
    : source.message || source.detail || fallback
  return {
    stage: extra.stage || source.stage || context?.currentTurn?.stage || 'request',
    code: source.code || source.category || extra.code || 'request_failed',
    message: redactSecrets(message),
    detail: redactSecrets(source.detail || source.message || message),
    retryable: source.retryable ?? extra.retryable ?? true,
    httpStatus: source.http_status ?? source.httpStatus ?? extra.httpStatus ?? null,
    requestId: source.request_id || source.requestId || null,
    incidentId: source.incident_id || source.incidentId || null,
  }
}

function finishStreamingInterrupted(context) {
  if (context.streamingItem) {
    context.streamingItem.streaming = false
    context.streamingItem.interrupted = true
    context.streamingItem = null
    return
  }
  const dangling = [...context.items].reverse().find((item) => item.streaming)
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

function upsertActivity(context, ev) {
  const turn = context.currentTurn
  if (!turn) return null
  const id = activityId(ev)
  let activity = context.activitiesById[id]
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
    context.activitiesById[id] = activity
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
    activity.error = normalizeError(
      ev.error, '本次尝试失败。', { stage: activity.stage }, context,
    )
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

  const step = context.stepsById[ev.operation_id] || context.stepsById[ev.step_id]
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

function appendTaskError(context, ev, fallback) {
  const error = normalizeError(ev.error || ev, fallback, { stage: ev.stage }, context)
  finishStreamingInterrupted(context)
  finishTurn(context, 'failed', {
    outcome: ev.outcome || 'failed', elapsedMs: ev.elapsed_ms, error,
  })
  const lastUser = [...context.items].reverse().find((item) => item.kind === 'user')
  const prompt = context.currentTurn?.prompt || lastUser?.text || ''
  const skillId = context.currentTurn?.skillId || lastUser?.skillId || ''
  const skillIds = context.currentTurn?.skillIds || lastUser?.skillIds || []
  const skillMode = context.currentTurn?.skillMode || lastUser?.skillMode || 'auto'
  const contextFiles = context.currentTurn?.contextFiles || lastUser?.contextFiles || []
  const contextMentions = context.currentTurn?.contextMentions || lastUser?.contextMentions || []
  const contentNodes = context.currentTurn?.contentNodes || lastUser?.contentNodes || []
  const previous = context.items[context.items.length - 1]
  if (previous?.kind === 'task_error' && previous.turnId === context.currentTurn?.id) {
    previous.error = error
    return
  }
  push(context, { kind: 'task_error', error, prompt, skillId, skillIds, skillMode,
                  contextFiles, contextMentions, contentNodes,
                  turnId: context.currentTurn?.id || '',
                  elapsedMs: ev.elapsed_ms ?? context.currentTurn?.elapsedMs ?? null })
}

function abortContextLoad(context) {
  if (!context) return
  context.loadRequest++
  context.loadController?.abort()
  context.loadController = null
  context.sessionLoading = false
}

function sessionSummaryModel(summary) {
  return summary?.model_context || summary?.session_model
    || (summary?.model && typeof summary.model === 'object' ? summary.model : null)
    || (summary?.provider_id && summary?.model_id ? summary : null)
}

function hasModelSelection(raw) {
  const source = raw?.agent || raw?.model_context?.agent || raw
  return Boolean(
    (source?.provider_id || source?.providerId)
    && (source?.model_id || source?.modelId),
  )
}

function activateSessionContext(context, summary = null) {
  const previous = activeContext.value
  if (previous && previous !== context) abortContextLoad(previous)
  activeContextKey.value = context.key
  if (!context.sessionId) {
    beginNewPermissionSession()
    beginNewModelSession()
    return
  }
  context.workspaceRoot ||= summary?.workspace_root || ''
  context.modelContext ||= sessionSummaryModel(summary)
  bindPermissionSession(context.sessionId, { workspaceRoot: context.workspaceRoot })
  bindModelSession(
    context.sessionId,
    hasModelSelection(context.modelContext) ? context.modelContext : null,
  )
}

export async function loadSession(id) {
  const sessionId = String(id || '').trim()
  if (!sessionId) return
  const summary = sessions.value.find((session) => session.id === sessionId)
  let context = findContext(sessionId)
  if (!context) {
    context = addContext({
      sessionId,
      title: summary?.title || '',
      workspaceRoot: summary?.workspace_root || '',
      hydrated: false,
    })
    context.modelContext = sessionSummaryModel(summary)
  }
  activateSessionContext(context, summary)

  if (context.hydrated || contextRunning(context)) {
    loadPermissionContext(sessionId).catch(() => {})
    if (!hasModelSelection(context.modelContext)) loadSessionModel(sessionId).catch(() => {})
    return
  }
  if (context.sessionLoading) return context.loadPromise

  const requestId = ++context.loadRequest
  context.sessionLoading = true
  const controller = new AbortController()
  context.loadController = controller
  const timeout = setTimeout(() => controller.abort(), SESSION_LOAD_TIMEOUT_MS)
  const loading = (async () => {
    try {
      const r = await apiFetch(`/api/sessions/${sessionId}/events`, {
        signal: controller.signal,
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const body = await r.json()
      if (requestId !== context.loadRequest) return
      for (const ev of body.events || []) {
        handleEvent({ type: ev.event_type, event_timestamp: ev.ts, ...ev.payload }, context)
      }
      context.hydrated = true
      // 历史 permission_changed 事件只用于回放；当前可见时再以服务端状态校准。
      if (!isActiveContext(context)) return
      await loadPermissionContext(sessionId, { signal: controller.signal }).catch(() => {})
      if (requestId !== context.loadRequest || !isActiveContext(context)) return
      if (controller.signal.aborted) throw new Error('任务上下文加载超时')
      await loadSessionModel(sessionId, { signal: controller.signal }).catch(() => {})
      if (controller.signal.aborted) throw new Error('任务上下文加载超时')
    } catch (error) {
      if (requestId === context.loadRequest) {
        push(context, {
          kind: 'fatal',
          error: controller.signal.aborted
            ? '任务上下文加载超时，请重新打开该任务。'
            : `任务记录读取失败：${error.message}`,
        })
      }
    } finally {
      clearTimeout(timeout)
      if (context.loadController === controller) context.loadController = null
      if (requestId === context.loadRequest) {
        context.sessionLoading = false
        context.loadPromise = null
      }
    }
  })()
  context.loadPromise = loading
  return loading
}

export async function sendMessage(text, {
  onUpdate,
  skillId = '',
  skillIds = [],
  skillMode,
  contextFiles = [],
  contextMentions = [],
  contentNodes = [],
} = {}) {
  const context = activeContext.value
  if (!context || contextRunning(context) || context.sessionLoading) return
  const hasContentNodes = Array.isArray(contentNodes) && contentNodes.length > 0
  const snapshot = hasContentNodes ? serializeEditorSnapshot(contentNodes) : null
  const prompt = snapshot?.message ?? String(text || '').trim()
  // 标签本身不是任务正文；只有引用而没有文字时不能发送。
  if (!(snapshot ? snapshot.plainText : prompt).trim()) return
  const legacySkillIds = normalizeSkillIds(skillIds, skillId)
  const inlineSkillIds = snapshot?.skillIds || []
  const requestedSkillIds = inlineSkillIds.length ? inlineSkillIds : legacySkillIds
  const wantsManualSkills = inlineSkillIds.length > 0
    || skillMode === 'manual'
    || (skillMode == null && legacySkillIds.length > 0)
  // 新界面不再提供 none，但旧审计记录的失败重试必须保持原语义；同理，
  // 没有行内标签的旧 manual 记录仍使用其结构化 skill_ids。
  const turnSkillMode = skillMode === 'none'
    ? 'none' : wantsManualSkills && requestedSkillIds.length ? 'manual' : 'auto'
  // 自动模式下的 skillId 可能来自上一轮服务端的路由结果。重试时必须
  // 重新路由，不能把模型曾经自动选中的 Skill 偷偷升级成人工强制选择。
  const turnSkillIds = turnSkillMode === 'manual' ? requestedSkillIds : []
  const turnSkillId = turnSkillIds[0] || ''
  const turnContextFiles = normalizeContextFiles([
    ...(snapshot?.contextFiles || []), ...normalizeContextFiles(contextFiles),
  ])
  const turnContextMentions = snapshot?.contextMentions?.length
    ? snapshot.contextMentions : normalizeContextMentions(contextMentions)
  const turnContentNodes = snapshot?.contentNodes
    || nodesFromMessageAndMentions(prompt, turnContextMentions)
  const previousTurn = context.currentTurn
  const turn = startTurn(context, prompt, {
    skillIds: turnSkillIds,
    skillMode: turnSkillMode,
    contextFiles: turnContextFiles,
    contextMentions: turnContextMentions,
    contentNodes: turnContentNodes,
  })
  const controller = new AbortController()
  context.controller = controller
  context.stepsById = {}
  context.confirmsById = {}
  context.streamingItem = null
  push(context, {
    kind: 'user', text: prompt, contentNodes: turnContentNodes,
    skillIds: turnSkillIds, skillId: turnSkillId, skillMode: turnSkillMode,
    contextFiles: turnContextFiles, contextMentions: turnContextMentions,
    routedSkillId: '', routedSkillName: '', skillResolved: false, turnId: turn.id,
  })
  let sawDone = false
  try {
    const includeDraftModel = !context.sessionId
    const resp = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: prompt, session_id: context.sessionId,
                             request_id: turn.id,
                             skill_id: turnSkillId || '',
                             skill_ids: turnSkillIds,
                             skill_mode: turnSkillMode,
                             context_files: contextFilePaths(turnContextFiles),
                             context_mentions: requestContextMentions(turnContextMentions),
                             ...(!context.sessionId ? permissionRequestPayload() : {}),
                             ...(includeDraftModel ? modelRequestPayload() : {}) }),
      signal: controller.signal,
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      const detailObject = body.detail && typeof body.detail === 'object'
        && !Array.isArray(body.detail) ? body.detail : null
      const detail = typeof body.detail === 'string' ? body.detail
        : detailObject?.message
          || (Array.isArray(body.detail) ? body.detail[0]?.msg : '')
      const error = new Error(detail || `请求失败（HTTP ${resp.status}）`)
      error.code = detailObject?.code || body.code || `http_${resp.status}`
      error.detail = detailObject?.detail || detail || error.message
      error.retryable = detailObject?.retryable
        ?? ([408, 425, 429].includes(resp.status) || resp.status >= 500)
      error.httpStatus = resp.status
      throw error
    }
    // 新会话 ID 同时由响应头提前返回。这样即使首个 session_created SSE
    // 到达前断流或用户停止，本地仍能在下一轮复用服务端已经创建的会话。
    const responseSessionId = String(resp.headers.get('X-Session-Id') || '').trim()
    if (responseSessionId && !context.sessionId) {
      registerContextSession(context, responseSessionId)
    }
    refreshSessions().catch(() => {})
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
        handleEvent(event, context)
      }
      if (isActiveContext(context)) onUpdate?.()
    }
    if (turn.stopRequested) {
      finishStreamingInterrupted(context)
      finishTurn(context, 'cancelled', { outcome: 'cancelled' })
      turn.transport = 'closed'
      return
    }
    if (!sawDone && !turn.stopRequested && !turn.terminalSeen) {
      const error = new Error('事件流提前结束，回复可能不完整。')
      error.code = 'stream_interrupted'
      throw error
    }
  } catch (e) {
    if (['workspace_busy', 'session_busy'].includes(e.code)) {
      context.items = context.items.filter((item) => item.turnId !== turn.id)
      context.currentTurn = previousTurn
      return {
        accepted: false,
        reason: e.code,
        message: e.message,
        contextKey: context.key,
      }
    }
    if (e.name === 'AbortError' || turn.stopRequested) {
      finishStreamingInterrupted(context)
      finishTurn(context, 'cancelled', { outcome: 'cancelled' })
      turn.transport = 'closed'
    } else if (!turn.terminalSeen) {
      turn.transport = 'broken'
      appendTaskError(context, {
        error: { code: e.code || 'connection_interrupted', message: e.message,
          detail: e.detail || e.message,
          retryable: e.retryable ?? (e.httpStatus == null || e.httpStatus >= 500),
          http_status: e.httpStatus },
        stage: turn.stage, elapsed_ms: Date.now() - turn.startedAt,
      }, '与服务的连接中断。')
    }
  } finally {
    turn.busy = false
    if (turn.transport !== 'broken') turn.transport = 'closed'
    if (context.controller === controller) context.controller = null
    context.updatedAt = Date.now()
    updateLocalSessionSummary(context)
    refreshSessions().catch(() => {})
  }
}

export function cancelCurrentTurn(sessionId = '') {
  const context = findContext(sessionId)
  const turn = context?.currentTurn
  if (!context || !turn || !contextRunning(context)) return
  turn.stopRequested = true
  turn.status = 'cancelling'
  finishStreamingInterrupted(context)

  for (const card of Object.values(context.confirmsById)) card.hidden = true
  for (const step of Object.values(context.stepsById)) {
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
    const step = context.stepsById[activity.stepId]
    activity.state = step?.status === 'result_unknown' ? 'result_unknown' : 'cancelled'
    activity.retryInMs = 0
    activity.deadlineAt = null
    activity.updatedAt = Date.now()
  }
  context.controller?.abort()
}

export async function retryMessage(text, options = {}) {
  const context = activeContext.value
  if (!context || contextRunning(context) || !text?.trim()) return
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
    const effectiveRisk = it.verification?.decision?.risk || it.risk
    return `- [${st}] **${it.tool}**：${it.purpose}（风险等级：${effectiveRisk}）\n  执行输出：${out}`
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
  const context = activeContext.value
  if (!context || contextRunning(context) || context.sessionLoading || !context.items.length) return
  context.nextIsReport = true
  const result = await sendMessage(_buildReportPrompt(), { onUpdate })
  if (result?.accepted === false) context.nextIsReport = false
  return result
}
