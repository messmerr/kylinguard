import assert from 'node:assert/strict'
import test from 'node:test'

import { alertBadgeText, resolveRuleChannels } from '../src/utils/alerts.js'

let fetchImpl = async (url) => {
  throw new Error(`unexpected fetch: ${url}`)
}
globalThis.fetch = (url, options = {}) => fetchImpl(url, options)

const alerts = await import('../src/composables/useAlerts.js')

function reset() {
  alerts._resetPendingAlertsForTests()
  fetchImpl = async (url) => { throw new Error(`unexpected fetch: ${url}`) }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

test('告警徽标隐藏零值并在超过 99 条时显示 99+', () => {
  assert.equal(alertBadgeText(0), '')
  assert.equal(alertBadgeText(-1), '')
  assert.equal(alertBadgeText(Number.NaN), '')
  assert.equal(alertBadgeText(1), '1')
  assert.equal(alertBadgeText(99), '99')
  assert.equal(alertBadgeText(100), '99+')
  assert.equal(alertBadgeText(240), '99+')
})

test('规则通知只解析仍然存在的渠道', () => {
  const channels = [
    { id: 2, name: '值班邮箱' },
    { id: 4, name: 'Webhook' },
  ]
  assert.deepEqual(
    resolveRuleChannels([4, 5, 2], channels),
    [channels[1], channels[0]],
  )
  assert.deepEqual(resolveRuleChannels([5], channels), [])
})

test('待处理告警刷新单飞并更新加载状态与数量', async () => {
  reset()
  const responseGate = deferred()
  let requestCount = 0
  fetchImpl = async (url, options) => {
    assert.equal(url, '/api/alerts')
    assert.equal(options.method, undefined)
    requestCount++
    await responseGate.promise
    return Response.json({ alerts: [{ id: 'a' }, { id: 'b' }] })
  }

  const first = alerts.refreshPendingAlerts()
  const concurrent = alerts.refreshPendingAlerts()
  assert.equal(first, concurrent)
  assert.equal(alerts.pendingAlertsLoading.value, true)
  assert.equal(alerts.pendingAlertsLoaded.value, false)
  assert.equal(requestCount, 1)

  responseGate.resolve()
  await first

  assert.equal(alerts.pendingAlertsLoading.value, false)
  assert.equal(alerts.pendingAlertsLoaded.value, true)
  assert.equal(alerts.pendingAlertsError.value, '')
  assert.equal(alerts.pendingAlertCount.value, 2)
})

test('待处理告警刷新失败时保留最近一次成功数据', async () => {
  reset()
  fetchImpl = async () => Response.json({ alerts: [{ id: 'kept' }] })
  await alerts.refreshPendingAlerts()

  fetchImpl = async () => Response.json(
    { detail: '告警服务暂时不可用' }, { status: 503 },
  )
  await assert.rejects(alerts.refreshPendingAlerts(), /告警服务暂时不可用/)

  assert.deepEqual(alerts.pendingAlerts.value, [{ id: 'kept' }])
  assert.equal(alerts.pendingAlertCount.value, 1)
  assert.equal(alerts.pendingAlertsLoaded.value, true)
  assert.equal(alerts.pendingAlertsLoading.value, false)
  assert.equal(alerts.pendingAlertsError.value, '告警服务暂时不可用')
})

test('并发确认同一告警只提交一次且全部成功后才移除', async () => {
  reset()
  alerts.pendingAlerts.value = [{ id: 'a' }, { id: 'b' }, { id: 'kept' }]
  const gates = new Map([['a', deferred()], ['b', deferred()]])
  const requests = []
  fetchImpl = async (url, options) => {
    requests.push({ url, options })
    const id = url.split('/').at(-2)
    await gates.get(id).promise
    return Response.json({ ok: true })
  }

  const first = alerts.acknowledgePendingAlert({ id: 'a', duplicateIds: ['a', 'b'] })
  const concurrent = alerts.acknowledgePendingAlert('a')
  assert.equal(first, concurrent)
  assert.equal(requests.length, 2)
  assert.equal(requests.every((request) => request.options.method === 'POST'), true)
  assert.equal(alerts.pendingAlertAckingIds.has('a'), true)
  assert.equal(alerts.pendingAlertAckingIds.has('b'), true)

  gates.get('a').resolve()
  await Promise.resolve()
  assert.equal(alerts.pendingAlertCount.value, 3)

  gates.get('b').resolve()
  await first
  assert.deepEqual(alerts.pendingAlerts.value, [{ id: 'kept' }])
  assert.equal(alerts.pendingAlertAckingIds.size, 0)
})

test('任一确认请求失败时保留本地告警', async () => {
  reset()
  alerts.pendingAlerts.value = [{ id: 'a' }, { id: 'b' }]
  fetchImpl = async (url) => url.includes('/b/')
    ? Response.json({ detail: '确认写入失败' }, { status: 500 })
    : Response.json({ ok: true })

  await assert.rejects(
    alerts.acknowledgePendingAlert({ id: 'a', duplicateIds: ['b'] }),
    /确认写入失败/,
  )

  assert.equal(alerts.pendingAlertCount.value, 2)
  assert.equal(alerts.pendingAlertAckingIds.size, 0)
})

test('确认与旧刷新交错时过滤已确认项但保留响应中的新告警', async () => {
  reset()
  alerts.pendingAlerts.value = [{ id: 'old' }]
  alerts.pendingAlertsLoaded.value = true
  const refreshGate = deferred()
  fetchImpl = async (url) => {
    if (url === '/api/alerts') {
      await refreshGate.promise
      return Response.json({ alerts: [{ id: 'old' }, { id: 'new' }] })
    }
    if (url === '/api/alerts/old/ack') return Response.json({ ok: true })
    throw new Error(`unexpected fetch: ${url}`)
  }

  const refresh = alerts.refreshPendingAlerts()
  await alerts.acknowledgePendingAlert('old')
  assert.deepEqual(alerts.pendingAlerts.value, [])

  refreshGate.resolve()
  await refresh
  assert.deepEqual(alerts.pendingAlerts.value, [{ id: 'new' }])
})

test('一键确认按服务端返回的准确 ID 移除并保留新告警', async () => {
  reset()
  alerts.pendingAlerts.value = [
    { id: 'rule:1' }, { id: 'system-1' }, { id: 'new' },
  ]
  alerts.pendingAlertsLoaded.value = true
  let bulkRequests = 0
  fetchImpl = async (url, options) => {
    if (url === '/api/alerts/ack-all') {
      bulkRequests++
      assert.equal(options.method, 'POST')
      return Response.json({
        ok: true,
        acknowledged_count: 2,
        acknowledged_ids: ['rule:1', 'system-1'],
      })
    }
    if (url === '/api/alerts') return Response.json({ alerts: [{ id: 'new' }] })
    throw new Error(`unexpected fetch: ${url}`)
  }

  const first = alerts.acknowledgeAllPendingAlerts()
  const concurrent = alerts.acknowledgeAllPendingAlerts()
  assert.equal(first, concurrent)
  assert.equal(alerts.pendingAlertsAcknowledgingAll.value, true)
  assert.equal(await first, 2)
  assert.equal(bulkRequests, 1)
  assert.deepEqual(alerts.pendingAlerts.value, [{ id: 'new' }])
  assert.equal(alerts.pendingAlertsAcknowledgingAll.value, false)
})

test('一键确认失败时保留当前待处理列表', async () => {
  reset()
  alerts.pendingAlerts.value = [{ id: 'rule:1' }]
  fetchImpl = async () => Response.json(
    { detail: '批量确认写入失败' }, { status: 500 },
  )

  await assert.rejects(alerts.acknowledgeAllPendingAlerts(), /批量确认写入失败/)
  assert.deepEqual(alerts.pendingAlerts.value, [{ id: 'rule:1' }])
  assert.equal(alerts.pendingAlertsAcknowledgingAll.value, false)
})

test('轮询立即刷新、每 30 秒复用单飞刷新并可停止', async () => {
  reset()
  const originalSetInterval = globalThis.setInterval
  const originalClearInterval = globalThis.clearInterval
  let intervalCallback = null
  let intervalDelay = null
  let setCalls = 0
  let clearedTimer = null
  let requestCount = 0
  globalThis.setInterval = (callback, delay) => {
    intervalCallback = callback
    intervalDelay = delay
    setCalls++
    return 77
  }
  globalThis.clearInterval = (timer) => { clearedTimer = timer }
  fetchImpl = async () => {
    requestCount++
    return Response.json({ alerts: [] })
  }

  try {
    alerts.startPendingAlertPolling()
    alerts.startPendingAlertPolling()
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(requestCount, 1)
    assert.equal(setCalls, 1)
    assert.equal(intervalDelay, 30_000)

    intervalCallback()
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(requestCount, 2)

    alerts.stopPendingAlertPolling()
    assert.equal(clearedTimer, 77)
  } finally {
    alerts._resetPendingAlertsForTests()
    globalThis.setInterval = originalSetInterval
    globalThis.clearInterval = originalClearInterval
  }
})
