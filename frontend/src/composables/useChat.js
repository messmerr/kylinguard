// 会话状态与 SSE 事件聚合：把细粒度审计事件流聚合成回合制渲染项。
// 同一状态机同时服务实时流（含 assistant_delta）与历史回放（无 delta，
// 用 plan.thought / final_answer.answer 整段渲染）。
import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useAuth.js'

export const sessions = ref([])
export const activeId = ref('')
export const items = ref([])
export const running = ref(false)
export const phase = ref(null) // 当前阶段指示：{ name, tool? }，null=空闲

let stepsById = {}
let confirmsById = {}
let streamingItem = null // 当前正在流式累积的 assistant 文本项
let _nextIsReport = false // 下一条 final_answer 标记为报告
let _loadRequest = 0

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
  // 定稿当前流式文本项；回放模式（无流）直接新建
  if (streamingItem) {
    streamingItem.role = role
    if (text) streamingItem.text = text
    streamingItem.streaming = false
    streamingItem = null
  } else if (text) {
    push({ kind: 'assistant', role, text, streaming: false })
  }
}

export function handleEvent(ev) {
  switch (ev.type) {
    case 'session_created':
      activeId.value = ev.session_id
      refreshSessions()
      break
    case 'phase':
      // 规划相位清空 tool（整体思考中）；审查相位带上工具名
      phase.value = ev.phase === 'reviewing'
        ? { name: 'reviewing', tool: ev.tool }
        : { name: ev.phase }
      // 审查相位：把对应步骤行标记为"审查中"，比笼统的"校验中"更具体
      if (ev.phase === 'reviewing' && stepsById[ev.step_id]) {
        stepsById[ev.step_id].status = 'reviewing'
      }
      break
    case 'snapshot':
      push({ kind: 'snapshot', snapshot: ev.snapshot,
             age: ev.collected_ago_seconds ?? 0, expanded: false })
      break
    case 'assistant_delta':
      phase.value = null // 首 token 到达，退出"规划中"指示
      if (!streamingItem) {
        streamingItem = push({ kind: 'assistant', role: 'streaming',
                               text: '', streaming: true })
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
            status: 'verifying', verification: null, output: null,
            durationMs: null, autoAllowed: false, denyReason: '',
            expanded: false,
          })
        }
      }
      // steps 为空时不定稿：等 final_answer 统一处理（文本即答案）
      break
    case 'verification': {
      phase.value = null // 判定已出，退出"审查中"指示
      const step = stepsById[ev.step_id]
      if (!step) break
      step.verification = { rule: ev.rule, review: ev.review, decision: ev.decision }
      if (ev.decision.action === 'deny') {
        step.status = 'denied'
        step.denyReason = ev.decision.reason
      } else if (ev.decision.action === 'auto') {
        step.status = 'running'
        step.autoAllowed = true
      } else {
        step.status = 'waiting'
      }
      break
    }
    case 'intent_filter':
      push({ kind: 'intent', decision: ev.decision, expanded: false })
      break
    case 'confirm_request':
      confirmsById[ev.confirm_id] = push({
        kind: 'confirm', confirmId: ev.confirm_id, stepId: ev.step_id,
        step: ev.step, decision: ev.decision, hidden: false,
      })
      break
    case 'confirm_result': {
      const card = confirmsById[ev.confirm_id]
      if (card) card.hidden = true
      const step = stepsById[ev.step_id]
      if (step) step.status = ev.approved ? 'running' : 'skipped'
      break
    }
    case 'execution': {
      const step = stepsById[ev.step_id]
      if (step) {
        step.status = 'done'
        step.output = ev.output
        step.durationMs = ev.duration_ms ?? null
      }
      break
    }
    case 'final_answer':
      finishStreaming('answer', ev.answer)
      if (ev.aborted) {
        const last = items.value[items.value.length - 1]
        if (last?.kind === 'assistant') last.aborted = true
      }
      if (_nextIsReport) {
        const last = items.value[items.value.length - 1]
        if (last?.kind === 'assistant') last.isReport = true
        _nextIsReport = false
      }
      break
    case 'fatal':
      streamingItem = null
      push({ kind: 'fatal', error: ev.error })
      break
    case 'user_query':
      // 回放模式渲染历史用户消息；实时模式已在发送时本地插入
      if (!running.value) push({ kind: 'user', text: ev.query })
      break
  }
}

export async function refreshSessions() {
  const r = await apiFetch('/api/sessions')
  sessions.value = (await r.json()).sessions
}

export function newSession() {
  if (running.value) return
  _loadRequest++
  resetSessionState()
}

function resetSessionState() {
  activeId.value = ''
  items.value = []
  stepsById = {}
  confirmsById = {}
  streamingItem = null
}

export async function loadSession(id) {
  if (running.value) return
  const requestId = ++_loadRequest
  resetSessionState()
  activeId.value = id
  try {
    const r = await apiFetch(`/api/sessions/${id}/events`)
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const body = await r.json()
    if (requestId !== _loadRequest || activeId.value !== id) return
    for (const ev of body.events || []) {
      handleEvent({ type: ev.event_type, ...ev.payload })
    }
  } catch (error) {
    if (requestId === _loadRequest && activeId.value === id) {
      push({ kind: 'fatal', error: `任务记录读取失败：${error.message}` })
    }
  }
}

export async function sendMessage(text, { onUpdate } = {}) {
  if (!text.trim() || running.value) return
  _loadRequest++
  running.value = true
  stepsById = {}
  confirmsById = {}
  streamingItem = null
  push({ kind: 'user', text })
  try {
    const resp = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: activeId.value }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        if (line.startsWith('data: ')) handleEvent(JSON.parse(line.slice(6)))
      }
      onUpdate?.()
    }
  } catch (e) {
    push({ kind: 'fatal', error: `连接中断：${e.message}` })
  } finally {
    running.value = false
    phase.value = null
    refreshSessions()
  }
}

export async function resolveConfirm(card, approved) {
  await apiFetch('/api/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_id: card.confirmId, approved }),
  })
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
