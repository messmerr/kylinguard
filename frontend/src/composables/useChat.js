// 会话状态与 SSE 事件聚合：把细粒度审计事件流聚合成回合制渲染项。
// 同一状态机同时服务实时流（含 assistant_delta）与历史回放（无 delta，
// 用 plan.thought / final_answer.answer 整段渲染）。
import { computed, reactive, ref } from 'vue'

export const sessions = ref([])
export const activeId = ref('')
export const items = ref([])
export const running = ref(false)

let stepsById = {}
let confirmsById = {}
let streamingItem = null // 当前正在流式累积的 assistant 文本项

export const stats = computed(() => {
  const s = { steps: 0, auto: 0, confirmed: 0, denied: 0 }
  for (const it of items.value) {
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
    case 'snapshot':
      push({ kind: 'snapshot', snapshot: ev.snapshot,
             age: ev.collected_ago_seconds ?? 0, expanded: false })
      break
    case 'assistant_delta':
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
  const r = await fetch('/api/sessions')
  sessions.value = (await r.json()).sessions
}

export function newSession() {
  if (running.value) return
  activeId.value = ''
  items.value = []
  stepsById = {}
  confirmsById = {}
  streamingItem = null
}

export async function loadSession(id) {
  if (running.value) return
  newSession()
  activeId.value = id
  const r = await fetch(`/api/sessions/${id}/events`)
  for (const ev of (await r.json()).events) {
    handleEvent({ type: ev.event_type, ...ev.payload })
  }
}

export async function sendMessage(text, { onUpdate } = {}) {
  if (!text.trim() || running.value) return
  running.value = true
  stepsById = {}
  confirmsById = {}
  streamingItem = null
  push({ kind: 'user', text })
  try {
    const resp = await fetch('/api/chat', {
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
    refreshSessions()
  }
}

export async function resolveConfirm(card, approved) {
  await fetch('/api/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_id: card.confirmId, approved }),
  })
}
