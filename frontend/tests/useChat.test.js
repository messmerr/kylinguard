import assert from 'node:assert/strict'
import test from 'node:test'

const localStorageWrites = []
globalThis.localStorage = {
  getItem: () => null,
  setItem: (key, value) => localStorageWrites.push([key, String(value)]),
  removeItem: () => {},
}

const chatResponses = []
const chatBodies = []
const sessionEventResponses = []
const sessionEventSignals = []
const permissionResponses = []
const permissionUpdateBodies = []
const modelResponses = []
let chatRequestCount = 0
let sessionEventsFixture = null
let permissionFixture = null
let sessionListFixture = []
let permissionCapabilitiesFixture
let modelConfigFixture = null
let sessionModelFixture = null

globalThis.fetch = async (url, options = {}) => {
  if (url === '/api/llm/config') {
    return Response.json(modelConfigFixture || { providers: [], defaults: {}, security: {} })
  }
  if (url.endsWith('/model')) {
    if (modelResponses.length) return modelResponses.shift()
    return sessionModelFixture
      ? Response.json(sessionModelFixture)
      : Response.json({}, { status: 404 })
  }
  if (url === '/api/sessions') {
    return Response.json({
      sessions: sessionListFixture,
      ...(permissionCapabilitiesFixture
        ? { permission_capabilities: permissionCapabilitiesFixture } : {}),
    })
  }
  if (url === '/api/confirm') {
    return Response.json({ ok: true })
  }
  if (url.endsWith('/events')) {
    sessionEventSignals.push(options.signal)
    if (sessionEventResponses.length) return sessionEventResponses.shift()
    if (sessionEventsFixture) return Response.json(sessionEventsFixture)
  }
  if (url === '/api/permissions') {
    if (permissionResponses.length) return permissionResponses.shift()
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body)
      permissionUpdateBodies.push(body)
      return Response.json({
        ...(permissionFixture || {}), ...body, version: body.version + 1,
      })
    }
    if (permissionFixture) return Response.json(permissionFixture)
    return Response.json({ mode: 'ask', version: 1, auto_review_roots: [] })
  }
  if (url.endsWith('/grants') && permissionFixture) {
    return Response.json({ grants: [] })
  }
  if (url !== '/api/chat') throw new Error(`unexpected fetch: ${url}`)
  chatRequestCount++
  chatBodies.push(JSON.parse(options.body))
  const next = chatResponses.shift()
  if (!next) throw new Error('missing controlled SSE response')
  return next.connect(options.signal)
}

const chat = await import('../src/composables/useChat.js')
const models = await import('../src/composables/useModels.js')
const permissions = await import('../src/composables/usePermissions.js')
const extensions = await import('../src/composables/useExtensions.js')
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

function controlledSse(headers = {}) {
  let controller
  let connectedSignal = null
  const stream = new ReadableStream({
    start(value) { controller = value },
  })
  return {
    connect(signal) {
      connectedSignal = signal
      signal?.addEventListener('abort', () => {
        controller.error(new DOMException('aborted', 'AbortError'))
      }, { once: true })
      return new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', ...headers },
      })
    },
    event(payload) {
      controller.enqueue(new TextEncoder().encode(
        `data: ${JSON.stringify(payload)}\n\n`,
      ))
    },
    close() { controller.close() },
    get signal() { return connectedSignal },
  }
}

function httpResponse(status, body) {
  return {
    connect() { return Response.json(body, { status }) },
  }
}

function deferredResponse() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function reset() {
  assert.equal(chat.running.value, false)
  chat._resetChatStateForTests()
  chatRequestCount = 0
  chatBodies.length = 0
  sessionEventResponses.length = 0
  sessionEventSignals.length = 0
  permissionResponses.length = 0
  permissionUpdateBodies.length = 0
  modelResponses.length = 0
  sessionEventsFixture = null
  permissionFixture = null
  sessionListFixture = []
  permissionCapabilitiesFixture = undefined
  modelConfigFixture = null
  sessionModelFixture = null
  localStorageWrites.length = 0
  chatResponses.length = 0
  chat.sessions.value = []
  permissions._resetPermissionStateForTests()
  models._resetModelStateForTests()
  extensions._resetExtensionStateForTests()
}

test('多 Skill 与行内服务器文件按有序 context_mentions 发送且不会扩展权限参数', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)

  const request = chat.sendMessage('检查磁盘', {
    contentNodes: [
      { type: 'text', text: '检查 ' },
      { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
      { type: 'text', text: ' 和 ' },
      { type: 'skill', id: 'log-review', label: '日志排查' },
      { type: 'text', text: '，参考 ' },
      {
        type: 'file', path: '/srv/work/logs/system.log',
        relativePath: 'logs/system.log', label: 'system.log',
      },
      { type: 'text', text: ' 的错误' },
    ],
  })
  await tick()
  assert.equal(chatBodies[0].message, '检查 @磁盘诊断 和 @日志排查，参考 @system.log 的错误')
  assert.equal(chatBodies[0].skill_id, 'disk-diagnosis')
  assert.deepEqual(chatBodies[0].skill_ids, ['disk-diagnosis', 'log-review'])
  assert.equal(chatBodies[0].skill_mode, 'manual')
  assert.deepEqual(chatBodies[0].context_files, ['logs/system.log'])
  assert.deepEqual(chatBodies[0].context_mentions, [
    { type: 'skill', offset: 3, skill_id: 'disk-diagnosis' },
    { type: 'skill', offset: 11, skill_id: 'log-review' },
    { type: 'file', offset: 20, path: 'logs/system.log' },
  ])
  assert.equal('permission_mode' in chatBodies[0], false)
  assert.equal('auto_review_roots' in chatBodies[0], false)

  sse.event({ type: 'final_answer', answer: '完成', aborted: false,
    outcome: 'completed', elapsed_ms: 5 })
  sse.event({ type: 'done' })
  sse.close()
  await request
})

test('没有显式 Skill 时发送 auto，自动路由结果只作为元信息', async () => {
  reset()
  const automatic = controlledSse()
  chatResponses.push(automatic)
  const automaticRequest = chat.sendMessage('自动处理')
  await tick()
  assert.equal(chatBodies[0].skill_mode, 'auto')
  assert.equal(chatBodies[0].skill_id, '')
  assert.deepEqual(chatBodies[0].skill_ids, [])
  assert.deepEqual(chatBodies[0].context_files, [])
  assert.deepEqual(chatBodies[0].context_mentions, [])
  automatic.event({ type: 'skill_not_selected', skill_mode: 'auto', reason: 'model_declined' })
  await tick()
  const automaticUser = chat.items.value.find((item) => item.kind === 'user')
  assert.equal(automaticUser.skillResolved, true)
  assert.equal(automaticUser.skillId, '')
  automatic.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  automatic.event({ type: 'done' })
  automatic.close()
  await automaticRequest
})

test('旧 none 回合重试保留禁用自动 Skill 的兼容语义', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('兼容旧回合', {
    skillMode: 'none',
    contentNodes: [{ type: 'text', text: '兼容旧回合' }],
  })
  await tick()
  assert.equal(chatBodies[0].skill_mode, 'none')
  assert.deepEqual(chatBodies[0].skill_ids, [])
  sse.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request
})

test('无行内标签的旧 manual 回合重试仍保留结构化 Skill', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('兼容旧手动 Skill', {
    skillMode: 'manual',
    skillIds: ['legacy-review'],
    contentNodes: [{ type: 'text', text: '兼容旧手动 Skill' }],
  })
  await tick()
  assert.equal(chatBodies[0].skill_mode, 'manual')
  assert.deepEqual(chatBodies[0].skill_ids, ['legacy-review'])
  sse.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request
})

test('确定性的 chat 4xx 保留服务端错误码且不建议重试', async () => {
  reset()
  chatResponses.push(httpResponse(409, {
    detail: {
      code: 'skill_required_tools_missing',
      message: '所选 Skill 缺少依赖工具。',
      retryable: false,
    },
  }))

  await chat.sendMessage('检查配置')

  const errorItem = chat.items.value.find((item) => item.kind === 'task_error')
  assert.equal(errorItem.error.code, 'skill_required_tools_missing')
  assert.equal(errorItem.error.retryable, false)
  assert.equal(errorItem.error.httpStatus, 409)
})

test('工作目录占用响应不会生成失败回合并返回可提示结果', async () => {
  reset()
  chatResponses.push(httpResponse(409, {
    detail: {
      code: 'workspace_busy',
      message: '该工作目录正在被另一个任务使用，当前对话仅可查看。',
      retryable: true,
    },
  }))

  const result = await chat.sendMessage('修改同一个文件')

  assert.equal(result.accepted, false)
  assert.equal(result.reason, 'workspace_busy')
  assert.equal(result.message, '该工作目录正在被另一个任务使用，当前对话仅可查看。')
  assert.equal(result.contextKey, chat.activeChatContextKey.value)
  assert.equal(chat.items.value.length, 0)
  assert.equal(chat.currentTurn.value, null)
  assert.equal(chat.running.value, false)
})

test('未发送草稿按对话隔离，迟到响应只能恢复原对话', () => {
  reset()
  const firstKey = chat.activeChatContextKey.value
  chat.setChatDraft([{ type: 'text', text: '对话 A 草稿' }])

  chat.newSession()
  assert.equal(chat.chatDraftNodes.value[0].text, '')
  chat.setChatDraft([{ type: 'text', text: '对话 B 草稿' }])

  chat.setChatDraft([{ type: 'text', text: '恢复后的 A 草稿' }], firstKey)
  assert.equal(chat.chatDraftNodes.value[0].text, '对话 B 草稿')
})

test('显式 Skill 和 context_mentions 在断流重试时保持不变', async () => {
  reset()
  const broken = controlledSse()
  chatResponses.push(broken)

  const firstRequest = chat.sendMessage('检查磁盘', {
    contentNodes: [
      { type: 'text', text: '用 ' },
      { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
      { type: 'text', text: ' 检查 ' },
      { type: 'file', relativePath: 'logs/system.log', label: 'system.log' },
    ],
  })
  await tick()
  broken.close()
  await firstRequest

  const errorItem = chat.items.value.find((item) => item.kind === 'task_error')
  assert.equal(errorItem.skillId, 'disk-diagnosis')
  assert.deepEqual(errorItem.skillIds, ['disk-diagnosis'])
  assert.equal(errorItem.skillMode, 'manual')
  assert.equal(errorItem.contextFiles[0].relativePath, 'logs/system.log')
  assert.equal(errorItem.contextMentions.length, 2)

  const retry = controlledSse()
  chatResponses.push(retry)
  const retryRequest = chat.retryMessage(errorItem.prompt, {
    skillId: errorItem.skillId,
    skillIds: errorItem.skillIds,
    skillMode: errorItem.skillMode,
    contextFiles: errorItem.contextFiles,
    contextMentions: errorItem.contextMentions,
    contentNodes: errorItem.contentNodes,
  })
  await tick()
  assert.equal(chatBodies[1].skill_id, 'disk-diagnosis')
  assert.deepEqual(chatBodies[1].skill_ids, ['disk-diagnosis'])
  assert.equal(chatBodies[1].skill_mode, 'manual')
  assert.deepEqual(chatBodies[1].context_files, ['logs/system.log'])
  assert.deepEqual(chatBodies[1].context_mentions, chatBodies[0].context_mentions)
  assert.equal(chatBodies[1].message, chatBodies[0].message)
  retry.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  retry.event({ type: 'done' })
  retry.close()
  await retryRequest
})

test('自动 Skill 回合重试会重新路由而不是变成人工指定', async () => {
  reset()
  const broken = controlledSse()
  chatResponses.push(broken)

  const firstRequest = chat.sendMessage('检查磁盘')
  await tick()
  broken.event({
    type: 'skill_selected', skill_id: 'disk-diagnosis',
    name: '磁盘诊断', skill_mode: 'auto',
  })
  broken.close()
  await firstRequest

  const errorItem = chat.items.value.find((item) => item.kind === 'task_error')
  assert.equal(errorItem.skillId, 'disk-diagnosis')
  assert.equal(errorItem.skillMode, 'auto')

  const retry = controlledSse()
  chatResponses.push(retry)
  const retryRequest = chat.retryMessage(errorItem.prompt, {
    skillId: errorItem.skillId,
    skillIds: errorItem.skillIds,
    skillMode: errorItem.skillMode,
    contextFiles: errorItem.contextFiles,
    contextMentions: errorItem.contextMentions,
    contentNodes: errorItem.contentNodes,
  })
  await tick()
  assert.equal(chatBodies[1].skill_mode, 'auto')
  assert.equal(chatBodies[1].skill_id, '')
  assert.deepEqual(chatBodies[1].skill_ids, [])

  retry.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  retry.event({ type: 'done' })
  retry.close()
  await retryRequest
})

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

test('不同会话可并行消费 SSE、切换查看且定向取消不会污染另一会话', async () => {
  reset()
  const first = controlledSse({ 'X-Session-Id': 'session-a' })
  const second = controlledSse({ 'X-Session-Id': 'session-b' })
  chatResponses.push(first, second)

  const firstRequest = chat.sendMessage('TASK-A')
  await tick()
  assert.equal(chat.activeId.value, 'session-a')
  assert.equal(chat.running.value, true)

  chat.newSession()
  assert.equal(first.signal.aborted, false)
  assert.equal(chat.activeId.value, '')
  const secondRequest = chat.sendMessage('TASK-B')
  await tick()
  assert.equal(chat.activeId.value, 'session-b')
  assert.equal(chatRequestCount, 2)
  assert.equal(chat.runningSessionIds.value.has('session-a'), true)
  assert.equal(chat.runningSessionIds.value.has('session-b'), true)

  first.event({ type: 'session_created', session_id: 'session-a' })
  first.event({ type: 'assistant_delta', text: 'A 正在处理' })
  first.event({ type: 'plan', thought: 'A 计划', final_answer: null, steps: [{
    step_id: 'shared-step', tool: 'files.write_a', arguments: {},
    purpose: '写入 A', risk: 'medium',
  }] })
  second.event({ type: 'assistant_delta', text: 'B 正在处理' })
  second.event({ type: 'plan', thought: 'B 计划', final_answer: null, steps: [{
    step_id: 'shared-step', tool: 'files.write_b', arguments: {},
    purpose: '写入 B', risk: 'medium',
  }] })
  await tick()

  assert.equal(chat.activeId.value, 'session-b')
  assert.equal(chat.items.value.find((item) => item.kind === 'step').tool, 'files.write_b')
  const requestCountBeforeBusyRetry = chatRequestCount
  await chat.sendMessage('TASK-B-SECOND-TURN')
  assert.equal(chatRequestCount, requestCountBeforeBusyRetry)

  await chat.loadSession('session-a')
  assert.equal(chat.activeId.value, 'session-a')
  assert.equal(chat.running.value, true)
  assert.equal(chat.items.value.find((item) => item.kind === 'step').tool, 'files.write_a')

  first.event({
    type: 'confirm_request', confirm_id: 'confirm-a', step_id: 'shared-step',
    step: { tool: 'files.write_a', arguments: {}, purpose: '写入 A', risk: 'medium' },
    decision: { action: 'confirm', risk: 'medium', reason: '需要确认' },
  })
  await tick()
  assert.equal(chat.sessionNeedsAttention('session-a'), true)

  await chat.loadSession('session-b')
  assert.equal(chat.activeId.value, 'session-b')
  assert.equal(chat.items.value.find((item) => item.kind === 'step').tool, 'files.write_b')
  chat.cancelCurrentTurn('session-a')
  await firstRequest
  assert.equal(first.signal.aborted, true)
  assert.equal(second.signal.aborted, false)
  assert.equal(chat.isSessionRunning('session-a'), false)
  assert.equal(chat.isSessionRunning('session-b'), true)
  assert.equal(chat.sessionNeedsAttention('session-a'), false)

  second.event({ type: 'final_answer', answer: 'B 完成', outcome: 'completed' })
  second.event({ type: 'done' })
  second.close()
  await secondRequest
  assert.equal(chat.isSessionRunning('session-b'), false)

  await chat.loadSession('session-a')
  assert.equal(chat.currentTurn.value.status, 'cancelled')
  assert.equal(chat.items.value.find((item) => item.kind === 'step').tool, 'files.write_a')
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

test('多步规划重试定稿时保留用户已经看到的前序文本', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('检查系统并继续处理')
  await tick()

  sse.event({ type: 'assistant_delta', text: '第一次规划尝试。' })
  sse.event({ type: 'assistant_delta', text: '\n\n' })
  sse.event({ type: 'assistant_delta', text: '第二次规划尝试。' })
  sse.event({
    type: 'plan', thought: '第二次规划尝试。', final_answer: null,
    steps: [{
      step_id: 'retry-step', tool: 'sysinfo.host_overview', arguments: {},
      purpose: '检查主机状态', risk: 'low',
    }],
  })
  await tick()

  const thinking = chat.items.value.find((item) => item.kind === 'assistant')
  assert.equal(thinking.role, 'thinking')
  assert.equal(thinking.text, '第一次规划尝试。\n\n第二次规划尝试。')

  sse.event({ type: 'assistant_delta', text: '第一次结论尝试。' })
  sse.event({ type: 'assistant_delta', text: '\n\n' })
  sse.event({ type: 'assistant_delta', text: '第二次结论尝试。' })
  sse.event({
    type: 'final_answer', answer: '第二次结论尝试。',
    outcome: 'completed', elapsed_ms: 20,
  })
  sse.event({ type: 'done' })
  sse.close()
  await request

  const replies = chat.items.value.filter((item) => item.kind === 'assistant')
  assert.equal(replies.length, 2)
  assert.equal(replies[1].role, 'answer')
  assert.equal(replies[1].text, '第一次结论尝试。\n\n第二次结论尝试。')
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

test('planning 进度只保留白名单活动与生成计数', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('WRITE-A-LONG-FILE')
  await tick()

  sse.event({
    type: 'progress', stage: 'planning', state: 'constructing_tool_call',
    operation_id: 'planning:0', activity: 'preparing_file_path',
    message: '不应直接采用后端自由文本', path: '/srv/private/note.md',
    content: '<html>不应进入前端状态</html>',
  })
  await tick()

  const activity = chat.currentTurn.value.activities.at(-1)
  assert.equal(activity.planningActivity, 'preparing_file_path')
  assert.equal(activity.generatedChars, null)
  assert.equal(activity.generatedBytes, null)
  assert.equal(Object.hasOwn(activity, 'message'), false)
  assert.equal(Object.hasOwn(activity, 'path'), false)
  assert.equal(Object.hasOwn(activity, 'content'), false)

  sse.event({
    type: 'progress', stage: 'planning', state: 'constructing_tool_call',
    operation_id: 'planning:0', activity: 'generating_file_content',
    generated_chars: 6709, generated_bytes: 7041,
  })
  await tick()

  assert.equal(chat.currentTurn.value.activities.length, 1)
  assert.equal(activity.planningActivity, 'generating_file_content')
  assert.equal(activity.generatedChars, 6709)
  assert.equal(activity.generatedBytes, 7041)

  sse.event({
    type: 'progress', stage: 'planning', state: 'retry_wait',
    operation_id: 'planning:0', attempt: 1, max_attempts: 3,
    error: { message: '模型服务暂时不可用' }, retry_in_ms: 1000,
  })
  sse.event({
    type: 'progress', stage: 'planning', state: 'connecting',
    operation_id: 'planning:0', attempt: 2, max_attempts: 3,
  })
  await tick()

  assert.equal(activity.planningActivity, '')
  assert.equal(activity.generatedChars, null)
  assert.equal(activity.generatedBytes, null)

  sse.event({
    type: 'progress', stage: 'planning', state: 'constructing_tool_call',
    operation_id: 'planning:0', activity: 'generating_file_content',
    generated_chars: 128, generated_bytes: 144,
  })
  sse.event({
    type: 'progress', stage: 'planning', state: 'retry_wait',
    operation_id: 'planning:0', attempt: 2, max_attempts: 3,
    error: { message: '模型流中断' }, retry_in_ms: 1000,
  })
  sse.event({
    type: 'progress', stage: 'planning', state: 'streaming',
    operation_id: 'planning:0', attempt: 3, max_attempts: 3,
  })
  await tick()

  assert.equal(activity.planningActivity, '')
  assert.equal(activity.generatedChars, null)
  assert.equal(activity.generatedBytes, null)

  sse.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request
})

test('未知工具在权限前标记为规划错误并允许模型重规划', async () => {
  reset()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('查看当前目录')
  await tick()

  sse.event({
    type: 'plan', thought: '先确认目录', final_answer: null,
    steps: [{
      step_id: 'bad-tool', tool: '服务器.run_command.run_command',
      arguments: { command: 'pwd' }, purpose: '确认当前目录', risk: 'low',
    }],
  })
  sse.event({
    type: 'capability_error', step_id: 'bad-tool', code: 'unknown_tool',
    capability: '服务器.run_command.run_command',
    message: '工具名称不存在，必须从清单逐字复制。', do_not_retry: false,
  })
  await tick()

  const step = chat.items.value.find((item) => item.kind === 'step')
  assert.equal(step.status, 'failed')
  assert.equal(step.failureStage, 'planning')
  assert.equal(step.expanded, true)
  assert.equal(step.error.code, 'unknown_tool')
  assert.equal(step.error.retryable, true)
  assert.equal(step.error.message, '所选工具当前不可用，系统正在调整方案。')
  assert.match(step.error.detail, /逐字复制/)

  sse.event({ type: 'final_answer', answer: '已重新规划。', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request
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

test('模型上下文事件会标记当轮回复用于历史辨识', async () => {
  reset()
  modelConfigFixture = {
    providers: [{
      id: 'provider-meta', name: 'Meta Provider', adapter: 'openai_compatible',
      base_url: 'https://api.example.com/v1', enabled: true,
      api_key_configured: true,
      models: [{ id: 'model-meta', label: 'Meta Model', enabled: true,
        supported_efforts: ['low', 'high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-meta', model_id: 'model-meta', reasoning_effort: 'low' },
      reviewer: { provider_id: 'provider-meta', model_id: 'model-meta', reasoning_effort: 'low' },
    },
    security: {},
  }
  await models.loadModelConfig()
  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('MODEL-META')
  await tick()
  sse.event({
    type: 'model_context', session_id: 'model-session', version: 1,
    provider_id: 'provider-meta', model_id: 'model-meta', reasoning_effort: 'high',
  })
  sse.event({ type: 'assistant_delta', text: '完成' })
  sse.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request

  const answer = chat.items.value.find((item) => item.kind === 'assistant')
  assert.equal(answer.model.modelLabel, 'Meta Model')
  assert.equal(answer.model.reasoningEffort, 'high')
})

test('历史多轮切换模型时每条回复保留各自模型快照', async () => {
  reset()
  modelConfigFixture = {
    providers: [{
      id: 'provider-a', name: 'Provider A', adapter: 'openai_compatible',
      base_url: 'https://a.example/v1', enabled: true, api_key_configured: true,
      models: [{ id: 'model-a', label: 'Model A', enabled: true,
        supported_efforts: ['low'] }],
    }, {
      id: 'provider-b', name: 'Provider B', adapter: 'openai_compatible',
      base_url: 'https://b.example/v1', enabled: true, api_key_configured: true,
      models: [{ id: 'model-b', label: 'Model B', enabled: true,
        supported_efforts: ['high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'low' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'auto' },
    },
    security: {},
  }
  await models.loadModelConfig()
  sessionEventsFixture = { events: [{
    event_type: 'user_query', payload: { query: '第一轮' },
  }, {
    event_type: 'model_context', payload: {
      agent: { provider_id: 'provider-a', provider_name: 'Provider A',
        model_id: 'model-a', reasoning_effort: 'low' },
      reviewer: {}, session_version: 1,
    },
  }, {
    event_type: 'final_answer', payload: { answer: '第一轮完成', outcome: 'completed' },
  }, {
    event_type: 'user_query', payload: { query: '第二轮' },
  }, {
    event_type: 'model_context', payload: {
      agent: { provider_id: 'provider-b', provider_name: 'Provider B',
        model_id: 'model-b', reasoning_effort: 'high' },
      reviewer: {}, session_version: 2,
    },
  }, {
    event_type: 'final_answer', payload: { answer: '第二轮完成', outcome: 'completed' },
  }] }
  permissionFixture = { mode: 'ask', version: 1 }
  sessionModelFixture = {
    session_id: 'history-models', provider_id: 'provider-b', model_id: 'model-b',
    reasoning_effort: 'high', version: 2,
  }

  await chat.loadSession('history-models')

  const answers = chat.items.value.filter((item) => item.kind === 'assistant')
  assert.equal(answers[0].model.modelId, 'model-a')
  assert.equal(answers[0].model.reasoningEffort, 'low')
  assert.equal(answers[1].model.modelId, 'model-b')
  assert.equal(answers[1].model.reasoningEffort, 'high')
})

test('历史 user_query 用有序 context_mentions 还原行内 Skill 和文件', async () => {
  reset()
  sessionEventsFixture = { events: [{
    event_type: 'user_query', payload: {
      query: '请用 @磁盘诊断 看😀 @a.log',
      skill_mode: 'manual',
      requested_skill_ids: ['disk-diagnosis'],
      context_files: ['logs/a.log'],
      context_mentions: [
        { type: 'skill', offset: 3, skill_id: 'disk-diagnosis', name: '磁盘诊断' },
        { type: 'file', offset: 12, path: 'logs/a.log', name: 'a.log' },
      ],
    },
  }] }
  permissionFixture = { mode: 'ask', version: 1 }

  await chat.loadSession('history-context')

  const user = chat.items.value.find((item) => item.kind === 'user')
  assert.deepEqual(user.skillIds, ['disk-diagnosis'])
  assert.deepEqual(user.contentNodes, [
    { type: 'text', text: '请用 ' },
    { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
    { type: 'text', text: ' 看😀 ' },
    { type: 'file', relativePath: 'logs/a.log', label: 'a.log' },
  ])
  assert.deepEqual(user.contextFiles.map((file) => file.relativePath), ['logs/a.log'])
})

test('无行内 mention 的多 Skill 历史保留每个服务端名称', async () => {
  reset()
  sessionEventsFixture = { events: [{
    event_type: 'user_query', payload: {
      query: '执行旧版复合工作流',
      skill_mode: 'manual',
      skill_ids: ['disk-diagnosis', 'log-review'],
    },
  }, {
    event_type: 'skill_selected', payload: {
      id: 'disk-diagnosis', name: '磁盘诊断', skill_mode: 'manual',
      position: 1, count: 2,
    },
  }, {
    event_type: 'skill_selected', payload: {
      id: 'log-review', name: '日志排查', skill_mode: 'manual',
      position: 2, count: 2,
    },
  }] }
  permissionFixture = { mode: 'ask', version: 1 }

  await chat.loadSession('history-multi-skill')

  const user = chat.items.value.find((item) => item.kind === 'user')
  assert.deepEqual(user.skillIds, ['disk-diagnosis', 'log-review'])
  assert.deepEqual(user.skillNames, ['磁盘诊断', '日志排查'])
})

test('切换到新任务后忽略旧会话迟到的权限与模型响应', async () => {
  reset()
  const permissionResponse = deferredResponse()
  const modelResponse = deferredResponse()
  permissionResponses.push(permissionResponse.promise)
  modelResponses.push(modelResponse.promise)
  permissions.bindPermissionSession('session-a')
  models.bindModelSession('session-a')

  const permissionLoad = permissions.loadPermissionContext('session-a')
  const modelLoad = models.loadSessionModel('session-a')
  await tick()
  chat.newSession()

  permissionResponse.resolve(Response.json({
    mode: 'auto_review', version: 7,
    workspace_root: '/srv/session-a', auto_review_roots: ['/srv/session-a'],
  }))
  modelResponse.resolve(Response.json({
    session_id: 'session-a', provider_id: 'provider-a', model_id: 'model-a',
    reasoning_effort: 'high', version: 4,
  }))
  const [permissionResult, modelResult] = await Promise.all([
    permissionLoad, modelLoad,
  ])

  assert.equal(permissionResult.reason, 'stale')
  assert.equal(modelResult.reason, 'stale')
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.mode, 'ask')
  assert.deepEqual(permissions.permissionContext.autoReviewRoots, [])
  assert.equal(permissions.permissionContext.workspaceRoot, '')
  assert.equal(permissions.permissionLoading.value, false)
  assert.equal(models.sessionModel.sessionId, '')
  assert.equal(models.sessionModel.modelId, '')
  assert.equal(models.sessionModel.loading, false)
})

test('切换任务后忽略仍在读取正文的旧权限响应', async () => {
  reset()
  const body = deferredResponse()
  permissionResponses.push({
    status: 200,
    ok: true,
    json: () => body.promise,
  })
  permissions.bindPermissionSession('session-a')

  const permissionLoad = permissions.loadPermissionContext('session-a')
  await tick()
  chat.newSession()
  body.resolve({
    mode: 'auto_review', version: 7,
    workspace_root: '/srv/session-a', auto_review_roots: ['/srv/session-a'],
  })
  const result = await permissionLoad

  assert.equal(result.reason, 'stale')
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.mode, 'ask')
  assert.deepEqual(permissions.permissionContext.autoReviewRoots, [])
  assert.equal(permissions.permissionContext.workspaceRoot, '')
})

test('切换任务会主动取消仍在等待的历史加载请求', async () => {
  reset()
  const eventsResponse = deferredResponse()
  sessionEventResponses.push(eventsResponse.promise)

  const loading = chat.loadSession('session-a')
  await tick()
  assert.equal(sessionEventSignals.length, 1)
  assert.equal(sessionEventSignals[0].aborted, false)

  chat.newSession()
  assert.equal(sessionEventSignals[0].aborted, true)
  eventsResponse.resolve(Response.json({ events: [] }))
  await loading
  assert.equal(chat.sessionLoading.value, false)
})

test('切换任务后全局权限修改仍生效，但旧会话模型修改被忽略', async () => {
  reset()
  modelConfigFixture = {
    providers: [{
      id: 'provider-a', name: 'Provider A', adapter: 'openai_compatible',
      base_url: 'https://a.example/v1', enabled: true, api_key_configured: true,
      models: [{ id: 'model-a', label: 'Model A', enabled: true,
        supported_efforts: ['low'] }],
    }, {
      id: 'provider-b', name: 'Provider B', adapter: 'openai_compatible',
      base_url: 'https://b.example/v1', enabled: true, api_key_configured: true,
      models: [{ id: 'model-b', label: 'Model B', enabled: true,
        supported_efforts: ['high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'low' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'auto' },
    },
    security: {},
  }
  await models.loadModelConfig()
  permissions.bindPermissionSession('session-a')
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', version: 1,
  })
  models.bindModelSession('session-a', {
    session_id: 'session-a', provider_id: 'provider-a', model_id: 'model-a',
    reasoning_effort: 'low', version: 1,
  })
  const permissionResponse = deferredResponse()
  const modelResponse = deferredResponse()
  permissionResponses.push(permissionResponse.promise)
  modelResponses.push(modelResponse.promise)

  const permissionChange = permissions.setPermissionMode('auto_review', {
    autoReviewRoots: ['/srv/session-a'],
  })
  const modelChange = models.setActiveModel({
    providerId: 'provider-b', modelId: 'model-b', reasoningEffort: 'high',
  })
  await tick()
  chat.newSession()
  permissionResponse.resolve(Response.json({
    mode: 'auto_review', version: 2,
    auto_review_roots: ['/srv/session-a'],
  }))
  modelResponse.resolve(Response.json({
    session_id: 'session-a', provider_id: 'provider-b', model_id: 'model-b',
    reasoning_effort: 'high', version: 2,
  }))
  const [permissionResult] = await Promise.all([permissionChange, modelChange])

  assert.equal(permissionResult.supported, true)
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.mode, 'auto_review')
  assert.deepEqual(permissions.permissionContext.autoReviewRoots, ['/srv/session-a'])
  assert.equal(models.sessionModel.sessionId, '')
  assert.equal(models.sessionModel.providerId, 'provider-a')
  assert.equal(models.sessionModel.modelId, 'model-a')
  assert.equal(models.sessionModel.saving, false)
})

test('历史权限同步期间切换新任务不会再启动旧会话模型读取', async () => {
  reset()
  sessionEventsFixture = { events: [] }
  const permissionResponse = deferredResponse()
  permissionResponses.push(permissionResponse.promise)

  const loading = chat.loadSession('session-a')
  await tick()
  assert.equal(permissions.permissionLoading.value, true)
  chat.newSession()
  permissionResponse.resolve(Response.json({
    mode: 'auto_review', version: 2,
    workspace_root: '/srv/session-a', auto_review_roots: ['/srv/session-a'],
  }))
  await loading

  assert.equal(chat.activeId.value, '')
  assert.equal(chat.sessionLoading.value, false)
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.mode, 'ask')
  assert.equal(models.sessionModel.sessionId, '')
  assert.equal(models.sessionModel.loading, false)
})

test('历史任务加载完成前阻止发送，完成后复用 ID 且不附带模型草稿', async () => {
  reset()
  modelConfigFixture = {
    providers: [{
      id: 'provider-a', name: 'Provider A', adapter: 'openai_compatible',
      base_url: 'https://api.example.com/v1', enabled: true,
      api_key_configured: true,
      models: [{ id: 'model-a', label: 'Model A', enabled: true,
        supported_efforts: ['high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'high' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'auto' },
    },
    security: {},
  }
  await models.loadModelConfig()
  permissionFixture = { mode: 'ask', version: 1 }
  const eventsResponse = deferredResponse()
  sessionEventResponses.push(eventsResponse.promise)

  const loading = chat.loadSession('history-session')
  await tick()
  assert.equal(chat.sessionLoading.value, true)
  await chat.sendMessage('TOO-EARLY')
  assert.equal(chatRequestCount, 0)

  eventsResponse.resolve(Response.json({ events: [{
    event_type: 'user_query', payload: { query: '历史问题' },
  }, {
    event_type: 'final_answer', payload: {
      answer: '历史回答', outcome: 'completed',
    },
  }] }))
  await loading
  assert.equal(chat.sessionLoading.value, false)
  assert.equal(chat.activeId.value, 'history-session')
  assert.equal(chat.items.value.some((item) => item.text === '历史问题'), true)
  assert.equal(models.sessionModel.synced, false)

  const sse = controlledSse()
  chatResponses.push(sse)
  const followUp = chat.sendMessage('继续')
  await tick()
  assert.equal(chatBodies[0].session_id, 'history-session')
  assert.equal('provider_id' in chatBodies[0], false)
  assert.equal('model_id' in chatBodies[0], false)
  assert.equal('reasoning_effort' in chatBodies[0], false)
  sse.event({ type: 'final_answer', answer: '继续完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await followUp
})

test('全局权限不随 chat 提交，服务器工作目录只随首轮创建会话', async () => {
  reset()
  permissionCapabilitiesFixture = { workspace_root: '/srv/default' }
  modelConfigFixture = {
    providers: [{
      id: 'provider-a', name: 'Provider A', adapter: 'openai_compatible',
      base_url: 'https://api.example.com/v1', enabled: true,
      api_key_configured: true,
      models: [{ id: 'model-a', label: 'Model A', enabled: true,
        supported_efforts: ['low', 'high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'high' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'low' },
    },
    security: {},
  }
  await chat.refreshSessions()
  await models.loadModelConfig()
  permissions.setDraftWorkspaceRoot('/srv/custom/../work')
  const first = controlledSse()
  chatResponses.push(first)
  const firstRequest = chat.sendMessage('CREATE-SESSION')
  await tick()
  assert.equal('permission_mode' in chatBodies[0], false)
  assert.equal('auto_review_roots' in chatBodies[0], false)
  assert.equal(chatBodies[0].workspace_root, '/srv/work')
  assert.equal(chatBodies[0].provider_id, 'provider-a')
  assert.equal(chatBodies[0].model_id, 'model-a')
  assert.equal(chatBodies[0].reasoning_effort, 'high')
  assert.equal(
    localStorageWrites.some(([, value]) => value.includes('/srv/work')),
    false,
  )
  first.event({
    type: 'session_created', session_id: 'session-permission',
    model_context: {
      session_id: 'session-permission', provider_id: 'provider-a',
      model_id: 'model-a', reasoning_effort: 'high', version: 1,
    },
  })
  first.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  first.event({ type: 'done' })
  first.close()
  await firstRequest

  const second = controlledSse()
  chatResponses.push(second)
  const secondRequest = chat.sendMessage('FOLLOW-UP')
  await tick()
  assert.equal(chatBodies[1].session_id, 'session-permission')
  assert.equal('permission_mode' in chatBodies[1], false)
  assert.equal('auto_review_roots' in chatBodies[1], false)
  assert.equal('workspace_root' in chatBodies[1], false)
  assert.equal('provider_id' in chatBodies[1], false)
  assert.equal('model_id' in chatBodies[1], false)
  assert.equal('reasoning_effort' in chatBodies[1], false)
  second.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  second.event({ type: 'done' })
  second.close()
  await secondRequest
})

test('任务列表能力元数据可在首条消息前控制完全访问入口', async () => {
  reset()
  permissionCapabilitiesFixture = {
    full_access_available: false,
    full_access_unavailable_reason: '部署方已关闭',
    execution_identity: 'ops',
    execution_identity_source: 'configured_exec_user',
    execution_account_separated: true,
    grants_root: false,
    workspace_root: '/srv/default-project',
  }

  await chat.refreshSessions()

  assert.equal(permissions.permissionContext.fullAccessAvailable, false)
  assert.equal(permissions.permissionContext.fullAccessUnavailableReason, '部署方已关闭')
  assert.equal(permissions.permissionContext.executorIdentity, 'ops')
  assert.equal(permissions.permissionContext.executionAccountSeparated, true)
  assert.equal(permissions.permissionContext.defaultWorkspaceRoot, '/srv/default-project')
  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/default-project')
})

test('首条消息前开启全局完全访问不会预创建会话', async () => {
  reset()
  permissionCapabilitiesFixture = {
    full_access_available: true,
    full_access_visible: true,
    execution_identity: 'backend-user',
    execution_identity_source: 'backend_process',
    workspace_root: '/srv/default-project',
  }
  modelConfigFixture = {
    providers: [{
      id: 'provider-full', name: 'Full Provider', adapter: 'openai_compatible',
      base_url: 'https://api.example.com/v1', enabled: true,
      api_key_configured: true,
      models: [{ id: 'model-full', label: 'Full Model', enabled: true,
        supported_efforts: ['medium', 'high'] }],
    }],
    defaults: {
      version: 1,
      agent: { provider_id: 'provider-full', model_id: 'model-full', reasoning_effort: 'high' },
      reviewer: { provider_id: 'provider-full', model_id: 'model-full', reasoning_effort: 'medium' },
    },
    security: {},
  }
  await chat.refreshSessions()
  await models.loadModelConfig()
  permissionFixture = {
    mode: 'ask', version: 1, auto_review_roots: [],
    full_access_available: true, full_access_visible: true,
  }
  permissions.setDraftWorkspaceRoot('/srv/custom/../project')
  const result = await chat.setChatPermissionMode('full_access')

  assert.equal(result.supported, true)
  assert.equal(chat.activeId.value, '')
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionMode.value, 'full_access')
  assert.equal(permissions.permissionContext.synced, true)
  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/project')
  assert.equal(chat.sessions.value.length, 0)
  assert.deepEqual(permissionUpdateBodies[0], {
    mode: 'full_access',
    version: 1,
    auto_review_roots: [],
  })

  const sse = controlledSse()
  chatResponses.push(sse)
  const request = chat.sendMessage('CREATE-FULL-ACCESS-SESSION')
  await tick()
  assert.equal(chatBodies[0].session_id, '')
  assert.equal(chatBodies[0].workspace_root, '/srv/project')
  assert.equal('password' in chatBodies[0], false)
  assert.equal('permission_mode' in chatBodies[0], false)
  assert.equal(chatBodies[0].provider_id, 'provider-full')
  sse.event({ type: 'session_created', session_id: 'global-full-session' })
  sse.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  sse.event({ type: 'done' })
  sse.close()
  await request
  assert.equal(chat.activeId.value, 'global-full-session')
})

test('已有任务采用会话工作目录并锁定，新任务恢复服务器默认目录', async () => {
  reset()
  permissionCapabilitiesFixture = { workspace_root: '/srv/default-project' }
  sessionListFixture = [{
    id: 'workspace-session', title: '已有任务',
    workspace_root: '/srv/locked-project', updated_at: 1,
  }]
  sessionEventsFixture = { events: [] }
  permissionFixture = { mode: 'ask', version: 1 }
  await chat.refreshSessions()

  await chat.loadSession('workspace-session')

  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/locked-project')
  assert.throws(
    () => permissions.setDraftWorkspaceRoot('/srv/another-project'),
    /任务创建后工作目录不可更改/,
  )

  chat.newSession()
  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/default-project')
  assert.equal(permissions.permissionContext.sessionId, '')
})

test('历史回放结束后以服务器当前持久权限覆盖历史事件', async () => {
  reset()
  sessionEventsFixture = { events: [{
    event_type: 'permission_changed',
    payload: {
      to_mode: 'full_access', version: 2,
    },
  }] }
  permissionFixture = {
    mode: 'full_access', version: 2, auto_review_roots: ['/srv/persistent'],
  }

  await chat.loadSession('history-permission')

  assert.equal(permissions.permissionMode.value, 'full_access')
  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/persistent'])
})

test('后端 permission_request/result 契约可生成并收起授权卡', () => {
  reset()
  chat.handleEvent({ type: 'plan', thought: '准备写入', steps: [{
    step_id: 'permission-step', tool: 'files.write_file',
    arguments: { path: '/srv/docs/note.md', content: 'hello' },
    purpose: '记录排查结论', risk: 'high',
  }] })
  chat.handleEvent({
    type: 'permission_request', request_id: 'request-contract',
    step_id: 'permission-step', context_version: 3,
    step: {
      tool: 'files.write_file',
      arguments: { path: '/srv/docs/note.md', content: 'hello' },
      purpose: '记录排查结论', risk: 'high',
    },
    decision: { action: 'double_confirm', risk: 'high', reason: '需要二次确认' },
    capability: 'files.write', resource: '/srv/docs/note.md',
    suggested_path: '/srv/docs', single_action_only: true,
    choices: ['deny', 'allow_once'],
    timeout_seconds: 300,
  })

  const card = chat.items.value.find((item) => item.kind === 'confirm')
  assert.equal(card.permissionRequestId, 'request-contract')
  assert.equal(card.contextVersion, 3)
  assert.deepEqual(card.choices, ['deny', 'allow_once'])
  assert.equal(card.operation.suggested_scope.path, '/srv/docs')

  chat.handleEvent({
    type: 'permission_result', request_id: 'request-contract',
    step_id: 'permission-step', decision: 'allow_once', approved: true,
  })
  assert.equal(card.hidden, true)
  assert.equal(chat.items.value.find((item) => item.kind === 'step').status, 'ready')
})

test('首个 SSE 前断流时通过响应头保留新会话并在下一轮复用', async () => {
  reset()
  const sessionId = 'a'.repeat(32)
  const broken = controlledSse({ 'X-Session-Id': sessionId })
  chatResponses.push(broken)

  const firstRequest = chat.sendMessage('FIRST-TURN')
  await tick()
  assert.equal(chat.activeId.value, sessionId)
  assert.equal(permissions.permissionContext.sessionId, sessionId)
  assert.equal(models.sessionModel.sessionId, sessionId)
  broken.close()
  await firstRequest

  const retry = controlledSse()
  chatResponses.push(retry)
  const retryRequest = chat.sendMessage('继续')
  await tick()
  assert.equal(chatBodies[1].session_id, sessionId)
  assert.equal('provider_id' in chatBodies[1], false)
  assert.equal('model_id' in chatBodies[1], false)
  retry.event({ type: 'final_answer', answer: '完成', outcome: 'completed' })
  retry.event({ type: 'done' })
  retry.close()
  await retryRequest
})

test('verification 保留工具基线风险及其来源用于权限解释', () => {
  reset()
  chat.handleEvent({ type: 'plan', thought: '读取第三方数据', steps: [{
    step_id: 'mcp-step', tool: 'custom-files.read', arguments: {},
    purpose: '读取数据', risk: 'low',
  }] })
  chat.handleEvent({
    type: 'verification', step_id: 'mcp-step',
    rule: { decision: 'allow', reason: '通过' },
    review: { safe: true, matches_intent: true, risk: 'low', reason: '只读' },
    decision: { action: 'auto', risk: 'low', reason: '允许自动执行' },
    tool_risk: { baseline: 'low', source: 'administrator', custom: true },
  })

  const step = chat.items.value.find((item) => item.kind === 'step')
  assert.deepEqual(step.verification.toolRisk, {
    baseline: 'low', source: 'administrator', custom: true,
  })
})
