import { computed, ref } from 'vue'
import { apiFetch } from './useApi.js'

const POLL_INTERVAL_MS = 30_000
const CLOCK_INTERVAL_MS = 1_000

export const systemStatus = ref(null)
export const systemStatusLoaded = ref(false)
export const systemStatusLoading = ref(false)
export const systemStatusError = ref('')
export const systemStatusReceivedAt = ref(0)
export const systemStatusClock = ref(Date.now())

export const systemStatusAgeSeconds = computed(() => {
  if (!systemStatus.value || !systemStatusReceivedAt.value) return 0
  const base = Number(systemStatus.value.collected_ago_seconds) || 0
  const elapsed = Math.max(
    0, systemStatusClock.value - systemStatusReceivedAt.value,
  ) / 1000
  return Math.max(0, base + elapsed)
})

export const systemStatusAgeText = computed(() => {
  const age = Math.round(systemStatusAgeSeconds.value)
  if (age < 3) return '刚刚'
  if (age < 60) return `${age} 秒前`
  if (age < 3600) return `${Math.floor(age / 60)} 分钟前`
  return `${Math.floor(age / 3600)} 小时前`
})

export const systemStatusIsStale = computed(() => (
  Boolean(systemStatus.value) && systemStatusAgeSeconds.value > 90
))

let refreshPromise = null
let pollingTimer = null
let clockTimer = null
let stateGeneration = 0

export function refreshSystemStatus({ force = false } = {}) {
  if (refreshPromise) return refreshPromise

  const generation = stateGeneration
  systemStatusLoading.value = true
  const operation = (async () => {
    const response = await apiFetch(force ? '/api/status?refresh=true' : '/api/status')
    if (!response.ok) throw new Error(`系统状态返回 HTTP ${response.status}`)
    const body = await response.json().catch(() => null)
    if (!body || typeof body !== 'object' || Array.isArray(body)
        || !body.snapshot || typeof body.snapshot !== 'object') {
      throw new Error('系统状态响应格式不正确')
    }
    if (generation === stateGeneration) {
      const receivedAt = Date.now()
      systemStatus.value = body
      systemStatusLoaded.value = true
      systemStatusError.value = ''
      systemStatusReceivedAt.value = receivedAt
      systemStatusClock.value = receivedAt
    }
    return body
  })()

  const tracked = operation.catch((error) => {
    if (generation === stateGeneration) {
      systemStatusError.value = error.message || '系统状态读取失败'
    }
    throw error
  }).finally(() => {
    if (generation === stateGeneration) systemStatusLoading.value = false
    if (refreshPromise === tracked) refreshPromise = null
  })
  refreshPromise = tracked
  return tracked
}

export function startSystemStatusPolling() {
  if (pollingTimer !== null) return
  refreshSystemStatus().catch(() => {})
  pollingTimer = setInterval(() => {
    refreshSystemStatus().catch(() => {})
  }, POLL_INTERVAL_MS)
  clockTimer = setInterval(() => {
    systemStatusClock.value = Date.now()
  }, CLOCK_INTERVAL_MS)
}

export function stopSystemStatusPolling() {
  if (pollingTimer !== null) clearInterval(pollingTimer)
  if (clockTimer !== null) clearInterval(clockTimer)
  pollingTimer = null
  clockTimer = null
}

export function _resetSystemStatusForTests() {
  stateGeneration++
  stopSystemStatusPolling()
  refreshPromise = null
  systemStatus.value = null
  systemStatusLoaded.value = false
  systemStatusLoading.value = false
  systemStatusError.value = ''
  systemStatusReceivedAt.value = 0
  systemStatusClock.value = Date.now()
}
