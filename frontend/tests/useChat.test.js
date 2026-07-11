import assert from 'node:assert/strict'
import test from 'node:test'

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
}

const chatResponses = []
let chatRequestCount = 0

globalThis.fetch = async (url, options = {}) => {
  if (url === '/api/sessions') {
    return Response.json({ sessions: [] })
  }
  if (url === '/api/confirm') {
    return Response.json({ ok: true })
  }
  if (url !== '/api/chat') throw new Error(`unexpected fetch: ${url}`)
  chatRequestCount++
  const next = chatResponses.shift()
  if (!next) throw new Error('missing controlled SSE response')
  return next.connect(options.signal)
}

const chat = await import('../src/composables/useChat.js')
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

function controlledSse() {
  let controller
  const stream = new ReadableStream({
    start(value) { controller = value },
  })
  return {
    connect(signal) {
      signal?.addEventListener('abort', () => {
        controller.error(new DOMException('aborted', 'AbortError'))
      }, { once: true })
      return new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    },
    event(payload) {
      controller.enqueue(new TextEncoder().encode(
        `data: ${JSON.stringify(payload)}\n\n`,
      ))
    },
    close() { controller.close() },
  }
}

function reset() {
  assert.equal(chat.running.value, false)
  chat.newSession()
  chatRequestCount = 0
  chatResponses.length = 0
}

test('业务终态到 done 之间仍禁止开启新回合', async () => {
  reset()
  const first = controlledSse()
  chatResponses.push(first)
  const firstRequest = chat.sendMessage('FIRST')
  await tick()

  first.event({ type: 'final_answer', answer: '完成', aborted: false,
    outcome: 'completed', elapsed_ms: 10 })
  await tick()
  assert.equal(chat.currentTurn.value.status, 'succeeded')
  assert.equal(chat.running.value, true)

  await chat.retryMessage('SECOND')
  assert.equal(chatRequestCount, 1)

  first.event({ type: 'done' })
  await tick()
  assert.equal(chat.running.value, true)
  first.close()
  await firstRequest
  assert.equal(chat.running.value, false)

  const second = controlledSse()
  chatResponses.push(second)
  const secondRequest = chat.sendMessage('SECOND')
  await tick()
  assert.equal(chatRequestCount, 2)
  second.event({ type: 'final_answer', answer: '第二轮完成', aborted: false,
    outcome: 'completed', elapsed_ms: 8 })
  second.event({ type: 'done' })
  second.close()
  await secondRequest
  assert.equal(chat.currentTurn.value.status, 'succeeded')
})

test('已有 final_answer 时缺少 done 不会覆盖为断流失败', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('FINAL-WITHOUT-DONE')
  await tick()
  sse.event({ type: 'final_answer', answer: '可靠终态', aborted: false,
    outcome: 'completed', elapsed_ms: 12 })
  sse.close()
  await request

  assert.equal(chat.currentTurn.value.status, 'succeeded')
  assert.equal(chat.running.value, false)
  assert.equal(chat.items.value.some((item) => item.kind === 'task_error'), false)
})

test('取消确认会隐藏确认卡并收尾排队步骤', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('CONFIRM-THEN-CANCEL')
  await tick()
  sse.event({ type: 'plan', thought: '准备操作', final_answer: null, steps: [{
    step_id: 'step-1', tool: 'services.restart_service', arguments: { name: 'demo' },
    purpose: '重启服务', risk: 'medium',
  }] })
  sse.event({ type: 'verification', step_id: 'step-1', rule: {}, review: {},
    decision: { action: 'confirm', risk: 'medium', reason: '需要确认' } })
  sse.event({ type: 'confirm_request', confirm_id: 'confirm-1', step_id: 'step-1',
    step: { tool: 'services.restart_service', arguments: { name: 'demo' },
      purpose: '重启服务', risk: 'medium' },
    decision: { action: 'confirm', reason: '需要确认' }, timeout_seconds: 300 })
  await tick()

  const confirmation = chat.items.value.find((item) => item.kind === 'confirm')
  const step = chat.items.value.find((item) => item.kind === 'step')
  assert.equal(confirmation.hidden, false)
  assert.equal(step.status, 'waiting')

  chat.cancelCurrentTurn()
  await request
  assert.equal(confirmation.hidden, true)
  assert.equal(step.status, 'cancelled')
  assert.equal(chat.currentTurn.value.status, 'cancelled')
  assert.equal(chat.running.value, false)
})

test('取消执行中的步骤会明确标记结果未知', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('EXECUTE-THEN-CANCEL')
  await tick()
  sse.event({ type: 'plan', thought: '准备执行', final_answer: null, steps: [{
    step_id: 'step-2', tool: 'disk.disk_hotspots', arguments: { path: '/var' },
    purpose: '分析磁盘', risk: 'low',
  }] })
  sse.event({ type: 'verification', step_id: 'step-2', rule: {}, review: {},
    decision: { action: 'auto', risk: 'low', reason: '只读' } })
  sse.event({ type: 'progress', stage: 'executing', state: 'connecting',
    operation_id: 'executing:step-2', step_id: 'step-2', elapsed_ms: 0 })
  await tick()

  const step = chat.items.value.find((item) => item.kind === 'step')
  assert.equal(step.status, 'running')
  chat.cancelCurrentTurn()
  await request
  assert.equal(step.status, 'result_unknown')
  assert.match(step.error.message, /结果暂时未知/)
})

test('task_error 与失败 final_answer 只渲染一个错误项', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('BAD-KEY')
  await tick()
  sse.event({ type: 'task_error', stage: 'planning', error: {
    code: 'llm_auth_invalid', message: '模型凭据无效。', retryable: false,
    http_status: 401, incident_id: 'err-test',
  } })
  sse.event({ type: 'final_answer', answer: '请检查 API Key。', aborted: true,
    outcome: 'failed', elapsed_ms: 5 })
  sse.event({ type: 'done' })
  sse.close()
  await request

  const errors = chat.items.value.filter((item) => item.kind === 'task_error')
  const answers = chat.items.value.filter((item) => item.kind === 'assistant')
  assert.equal(errors.length, 1)
  assert.equal(errors[0].answer, '请检查 API Key。')
  assert.equal(answers.length, 0)
})
