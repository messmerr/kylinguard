import assert from 'node:assert/strict'
import test from 'node:test'

let fetchImpl = async (url) => {
  throw new Error(`unexpected fetch: ${url}`)
}
globalThis.fetch = (url, options = {}) => fetchImpl(url, options)

const status = await import('../src/composables/useSystemStatus.js')

function reset() {
  status._resetSystemStatusForTests()
  fetchImpl = async (url) => { throw new Error(`unexpected fetch: ${url}`) }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

test('系统状态刷新单飞并共享同一份快照', async () => {
  reset()
  const gate = deferred()
  let requests = 0
  fetchImpl = async (url) => {
    assert.equal(url, '/api/status')
    requests++
    await gate.promise
    return Response.json({ snapshot: { memory: 'shared' }, collected_ago_seconds: 2 })
  }

  const first = status.refreshSystemStatus()
  const concurrent = status.refreshSystemStatus()
  assert.equal(first, concurrent)
  assert.equal(status.systemStatusLoading.value, true)
  assert.equal(requests, 1)

  gate.resolve()
  await first
  assert.equal(status.systemStatus.value.snapshot.memory, 'shared')
  assert.equal(status.systemStatusLoaded.value, true)
  assert.equal(status.systemStatusError.value, '')
})

test('系统状态刷新失败时保留最近一次成功快照', async () => {
  reset()
  fetchImpl = async () => Response.json({
    snapshot: { memory: 'kept' }, collected_ago_seconds: 1,
  })
  await status.refreshSystemStatus()

  fetchImpl = async () => Response.json({}, { status: 503 })
  await assert.rejects(status.refreshSystemStatus(), /HTTP 503/)
  assert.equal(status.systemStatus.value.snapshot.memory, 'kept')
  assert.match(status.systemStatusError.value, /HTTP 503/)
  assert.equal(status.systemStatusLoaded.value, true)
})

test('系统状态轮询只创建一组定时器并可停止', async () => {
  reset()
  const originalSetInterval = globalThis.setInterval
  const originalClearInterval = globalThis.clearInterval
  const callbacks = []
  const cleared = []
  globalThis.setInterval = (callback, delay) => {
    callbacks.push({ callback, delay })
    return callbacks.length
  }
  globalThis.clearInterval = (id) => { cleared.push(id) }
  fetchImpl = async () => Response.json({ snapshot: {}, collected_ago_seconds: 0 })

  try {
    status.startSystemStatusPolling()
    status.startSystemStatusPolling()
    await Promise.resolve()
    assert.deepEqual(callbacks.map(item => item.delay), [30_000, 1_000])
    status.stopSystemStatusPolling()
    assert.deepEqual(cleared, [1, 2])
  } finally {
    globalThis.setInterval = originalSetInterval
    globalThis.clearInterval = originalClearInterval
    status._resetSystemStatusForTests()
  }
})
