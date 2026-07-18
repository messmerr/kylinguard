import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useApi.js'

const POLL_INTERVAL_MS = 30_000

export const pendingAlerts = ref([])
export const pendingAlertCount = computed(() => pendingAlerts.value.length)
export const pendingAlertsLoaded = ref(false)
export const pendingAlertsLoading = ref(false)
export const pendingAlertsError = ref('')
export const pendingAlertAckingIds = reactive(new Set())
export const pendingAlertsAcknowledgingAll = ref(false)

let refreshPromise = null
let acknowledgeAllPromise = null
let pollingTimer = null
let stateGeneration = 0
const ackPromises = new Map()
const locallyAcknowledgedIds = new Set()

function detailMessage(body, fallback) {
  if (typeof body?.detail === 'string') return body.detail
  if (typeof body?.detail?.message === 'string') return body.detail.message
  if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === 'string') {
    return body.detail[0].msg
  }
  if (typeof body?.message === 'string') return body.message
  return fallback
}

async function readJson(response, fallback) {
  try {
    return await response.json()
  } catch {
    throw new Error(fallback)
  }
}

function alertIds(alert) {
  const primaryId = typeof alert === 'object' && alert !== null ? alert.id : alert
  const duplicateIds = typeof alert === 'object' && alert !== null
    && Array.isArray(alert.duplicateIds) ? alert.duplicateIds : []
  return [...new Set([primaryId, ...duplicateIds]
    .map((id) => String(id ?? '').trim()).filter(Boolean))]
}

export function refreshPendingAlerts() {
  if (refreshPromise) return refreshPromise

  const generation = stateGeneration
  pendingAlertsLoading.value = true
  pendingAlertsError.value = ''

  const operation = (async () => {
    const response = await apiFetch('/api/alerts')
    const body = await readJson(
      response, '待处理告警读取失败：服务器返回的数据无法解析',
    )
    if (!response.ok) {
      throw new Error(detailMessage(
        body, `待处理告警读取失败（HTTP ${response.status}）`,
      ))
    }
    if (!Array.isArray(body.alerts)) throw new Error('待处理告警数据格式不正确')

    if (generation === stateGeneration) {
      const responseIds = new Set(body.alerts.map(
        (alert) => String(alert?.id ?? ''),
      ))
      pendingAlerts.value = body.alerts.filter(
        (alert) => !locallyAcknowledgedIds.has(String(alert?.id ?? '')),
      )
      for (const id of locallyAcknowledgedIds) {
        if (!responseIds.has(id)) locallyAcknowledgedIds.delete(id)
      }
      pendingAlertsLoaded.value = true
    }
    return pendingAlerts.value
  })()

  const tracked = operation.catch((error) => {
    if (generation === stateGeneration) {
      pendingAlertsError.value = error.message || '待处理告警读取失败'
    }
    throw error
  }).finally(() => {
    if (generation === stateGeneration) pendingAlertsLoading.value = false
    if (refreshPromise === tracked) refreshPromise = null
  })
  refreshPromise = tracked
  return tracked
}

export function acknowledgePendingAlert(alert) {
  const ids = alertIds(alert)
  if (!ids.length) return Promise.reject(new Error('告警 ID 无效'))

  const existing = ids.map((id) => ackPromises.get(id)).find(Boolean)
  if (existing) return existing

  const generation = stateGeneration
  const operation = (async () => {
    const responses = await Promise.all(ids.map((id) => (
      apiFetch(`/api/alerts/${encodeURIComponent(id)}/ack`, { method: 'POST' })
    )))
    const failed = responses.find((response) => !response.ok)
    if (failed) {
      const body = await failed.json().catch(() => ({}))
      throw new Error(detailMessage(
        body, `告警标记失败（HTTP ${failed.status}）`,
      ))
    }

    if (generation === stateGeneration) {
      const acknowledgedIds = new Set(ids)
      for (const id of acknowledgedIds) locallyAcknowledgedIds.add(id)
      pendingAlerts.value = pendingAlerts.value.filter(
        (item) => !acknowledgedIds.has(String(item?.id ?? '')),
      )
    }
    return true
  })()

  let tracked
  tracked = operation.finally(() => {
    for (const id of ids) {
      if (ackPromises.get(id) !== tracked) continue
      ackPromises.delete(id)
      pendingAlertAckingIds.delete(id)
    }
  })
  for (const id of ids) {
    ackPromises.set(id, tracked)
    pendingAlertAckingIds.add(id)
  }
  return tracked
}

export function acknowledgeAllPendingAlerts() {
  if (acknowledgeAllPromise) return acknowledgeAllPromise

  const generation = stateGeneration
  pendingAlertsAcknowledgingAll.value = true
  const operation = (async () => {
    const response = await apiFetch('/api/alerts/ack-all', { method: 'POST' })
    const body = await readJson(
      response, '待处理告警批量确认失败：服务器返回的数据无法解析',
    )
    if (!response.ok) {
      throw new Error(detailMessage(
        body, `待处理告警批量确认失败（HTTP ${response.status}）`,
      ))
    }
    if (!Array.isArray(body.acknowledged_ids)) {
      throw new Error('待处理告警批量确认结果格式不正确')
    }

    const acknowledgedIds = new Set(
      body.acknowledged_ids.map(id => String(id ?? '')).filter(Boolean),
    )
    if (generation === stateGeneration) {
      for (const id of acknowledgedIds) locallyAcknowledgedIds.add(id)
      pendingAlerts.value = pendingAlerts.value.filter(
        item => !acknowledgedIds.has(String(item?.id ?? '')),
      )
    }
    await refreshPendingAlerts().catch(() => {})
    return acknowledgedIds.size
  })()

  const tracked = operation.finally(() => {
    if (generation === stateGeneration) pendingAlertsAcknowledgingAll.value = false
    if (acknowledgeAllPromise === tracked) acknowledgeAllPromise = null
  })
  acknowledgeAllPromise = tracked
  return tracked
}

export function startPendingAlertPolling() {
  if (pollingTimer !== null) return
  refreshPendingAlerts().catch(() => {})
  pollingTimer = setInterval(() => {
    refreshPendingAlerts().catch(() => {})
  }, POLL_INTERVAL_MS)
}

export function stopPendingAlertPolling() {
  if (pollingTimer === null) return
  clearInterval(pollingTimer)
  pollingTimer = null
}

export function _resetPendingAlertsForTests() {
  stateGeneration++
  stopPendingAlertPolling()
  refreshPromise = null
  acknowledgeAllPromise = null
  ackPromises.clear()
  locallyAcknowledgedIds.clear()
  pendingAlerts.value = []
  pendingAlertsLoaded.value = false
  pendingAlertsLoading.value = false
  pendingAlertsError.value = ''
  pendingAlertAckingIds.clear()
  pendingAlertsAcknowledgingAll.value = false
}
