<template>
  <el-dialog
    id="system-status-panel"
    :model-value="open"
    class="status-dialog"
    width="760px"
    align-center
    append-to-body
    :lock-scroll="true"
    :close-on-click-modal="true"
    :close-on-press-escape="true"
    @close="emit('close')"
    @closed="emit('closed')"
  >
    <template #header="{ titleId, titleClass }">
      <div class="dialog-heading">
        <span class="heading-icon"><KgIcon name="gauge" :size="19" /></span>
        <div>
          <h2 :id="titleId" :class="titleClass">系统状态</h2>
          <p :class="{ 'is-stale': statusError || isStale }">{{ statusHeaderText }}</p>
        </div>
      </div>
    </template>

    <div class="status-content">
      <section class="panel-section" aria-labelledby="host-status-title">
        <div class="section-head">
          <span id="host-status-title" class="section-title">
            <KgIcon name="server" :size="15" />主机资源
          </span>
          <span v-if="status" class="age">
            {{ statusError || isStale ? '等待恢复' : '每 30 秒刷新' }}
          </span>
        </div>

        <template v-if="status">
          <div class="resource-grid">
            <button
              v-for="metric in resourceMetrics"
              :key="metric.key"
              class="resource-card"
              :class="[`is-${metric.tone}`, { 'is-selected': expanded === metric.key }]"
              type="button"
              :aria-expanded="expanded === metric.key"
              :aria-controls="`${metric.key}-detail`"
              @click="toggleDetail(metric.key)"
            >
              <span class="resource-card-head">
                <span class="metric-icon"><KgIcon :name="metric.icon" :size="17" /></span>
                <span class="metric-label">{{ metric.label }}</span>
                <strong>{{ metric.value }}</strong>
              </span>
              <el-progress
                :percentage="metric.percent ?? 0"
                :show-text="false"
                :stroke-width="7"
                :color="progressColor(metric.tone)"
                :class="{ 'is-unavailable': metric.percent == null }"
              />
              <span class="metric-note">
                <span>{{ metric.note }}</span>
                <KgIcon
                  name="chevron"
                  :size="13"
                  :class="{ open: expanded === metric.key }"
                />
              </span>
            </button>
          </div>

          <div class="signal-grid" aria-label="运行状态计数">
            <button
              v-for="metric in signalMetrics"
              :key="metric.key"
              class="signal-card"
              :class="[`is-${metric.tone}`, { 'is-selected': expanded === metric.key }]"
              type="button"
              :aria-expanded="expanded === metric.key"
              :aria-controls="`${metric.key}-detail`"
              @click="toggleDetail(metric.key)"
            >
              <span class="signal-icon"><KgIcon :name="metric.icon" :size="18" /></span>
              <span class="signal-copy">
                <span>{{ metric.label }}</span>
                <small>{{ metric.note }}</small>
              </span>
              <strong>{{ metric.value }}</strong>
              <KgIcon
                name="chevron"
                :size="13"
                class="signal-chevron"
                :class="{ open: expanded === metric.key }"
              />
            </button>
          </div>

          <Transition name="detail">
            <div
              v-if="expandedMetric"
              :id="`${expandedMetric.key}-detail`"
              class="metric-detail"
              role="region"
              :aria-label="`${expandedMetric.label}明细`"
            >
              <div class="detail-head">
                <span><KgIcon :name="expandedMetric.icon" :size="15" />{{ expandedMetric.label }}明细</span>
                <button type="button" aria-label="收起明细" title="收起" @click="expanded = ''">
                  <KgIcon name="close" :size="14" />
                </button>
              </div>
              <div v-if="expandedLines.length" class="detail-lines">
                <div v-for="(line, index) in expandedLines" :key="`${index}-${line}`">{{ line }}</div>
              </div>
              <p v-else class="empty-detail">暂无需要展示的明细</p>
            </div>
          </Transition>
        </template>

        <div v-else-if="statusError" class="status-error" role="status">
          <KgIcon name="warning" :size="20" />
          <span>暂时无法读取主机状态</span>
          <el-button size="small" @click="poll">重试</el-button>
        </div>
        <el-skeleton v-else :rows="3" animated class="status-loading" />
      </section>

      <section v-if="alerts.length" class="panel-section alert-section" aria-labelledby="alert-title">
        <div class="section-head">
          <span id="alert-title" class="section-title">
            <KgIcon name="bell" :size="15" />待处理告警
          </span>
          <span class="alert-count">{{ alerts.length }}</span>
        </div>
        <div class="alert-list">
          <article
            v-for="alert in alerts"
            :key="alert.id"
            class="alert-card"
            :class="alert.severity === 'critical' ? 'critical' : 'warning'"
          >
            <span class="alert-symbol"><KgIcon name="warning" :size="16" /></span>
            <div class="alert-copy">
              <div class="alert-head">
                <strong>{{ alert.title }}</strong>
                <span>{{ alert.metric }}</span>
              </div>
              <p>{{ alert.message }}</p>
            </div>
            <el-button size="small" plain @click.stop="ack(alert)">标记已读</el-button>
          </article>
        </div>
      </section>

      <section class="panel-section" aria-labelledby="task-status-title">
        <div class="section-head">
          <span id="task-status-title" class="section-title">
            <KgIcon name="task" :size="15" />当前任务
          </span>
          <span class="section-meta">本次运行</span>
        </div>
        <div class="stat-grid">
          <article v-for="item in taskStats" :key="item.label" class="stat">
            <span class="stat-icon" :class="item.tone">
              <KgIcon :name="item.icon" :size="15" />
            </span>
            <span>
              <strong :class="item.tone">{{ item.value }}</strong>
              <small>{{ item.label }}</small>
            </span>
          </article>
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiFetch } from '../composables/useApi.js'
import { stats } from '../composables/useChat.js'
import KgIcon from './KgIcon.vue'

const props = defineProps({ open: { type: Boolean, default: true } })
const emit = defineEmits(['close', 'closed'])

const status = ref(null)
const alerts = ref([])
const expanded = ref('')
const statusError = ref(false)
const statusReceivedAt = ref(0)
const clock = ref(Date.now())
let timer = null
let alertTimer = null
let clockTimer = null

const snapshot = computed(() => status.value?.snapshot || {})

const resourceMetrics = computed(() => [
  loadMetric(snapshot.value.uptime_load),
  percentMetric(
    'memory', '内存', 'memory', memoryPercent(snapshot.value.memory),
    snapshot.value.memory, '物理内存已用',
  ),
  percentMetric(
    'disk', '磁盘', 'disk', diskPercent(snapshot.value.disk),
    snapshot.value.disk, '占用最高分区',
  ),
])

const signalMetrics = computed(() => [
  countMetric(
    'failed_units', '失败服务', 'service', snapshot.value.failed_units,
    'failed_units', '未发现失败服务', '需要尽快检查', 'danger',
  ),
  countMetric(
    'top_cpu', 'CPU 进程', 'process', snapshot.value.top_cpu,
    'top_cpu', '暂无进程数据', '按 CPU 占用排序', 'info',
  ),
  countMetric(
    'recent_errors', '错误日志', 'log', snapshot.value.recent_errors,
    'recent_errors', '近期无错误', '近期错误记录', 'warning',
  ),
])

const allMetrics = computed(() => [...resourceMetrics.value, ...signalMetrics.value])
const expandedMetric = computed(() => (
  allMetrics.value.find((metric) => metric.key === expanded.value) || null
))
const expandedLines = computed(() => detailLines(expandedMetric.value?.raw))

const taskStats = computed(() => [
  { label: '工具调用', value: stats.value.steps, tone: 'info', icon: 'terminal' },
  { label: '自动执行', value: stats.value.auto, tone: 'success', icon: 'check' },
  { label: '人工确认', value: stats.value.confirmed, tone: 'warning', icon: 'shield' },
  { label: '已拦截', value: stats.value.denied, tone: 'danger', icon: 'warning' },
])

function loadMetric(raw = '') {
  if (isUnavailable(raw)) return unavailableMetric('uptime_load', 'CPU / 负载', 'gauge', raw)
  const cpu = raw.match(/CPU:\s*(\d+(?:\.\d+)?)%/i)
  if (cpu) {
    return percentMetric('uptime_load', 'CPU / 负载', 'gauge', Number(cpu[1]), raw, 'CPU 使用率')
  }
  const load = raw.match(/load average[s]?:\s*([\d.]+)/i)
  if (load) {
    return percentMetric(
      'uptime_load', 'CPU / 负载', 'gauge', Number(load[1]) * 100,
      raw, '1 分钟负载（按单核折算）',
    )
  }
  return unavailableMetric('uptime_load', 'CPU / 负载', 'gauge', raw)
}

function percentMetric(key, label, icon, percent, raw = '', note = '') {
  if (percent == null || Number.isNaN(percent) || isUnavailable(raw)) {
    return unavailableMetric(key, label, icon, raw)
  }
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)))
  return {
    key,
    label,
    icon,
    value: `${safePercent}%`,
    percent: safePercent,
    note,
    tone: usageTone(safePercent),
    raw,
  }
}

function unavailableMetric(key, label, icon, raw = '') {
  return {
    key,
    label,
    icon,
    value: '—',
    percent: null,
    note: '数据暂不可用',
    tone: 'neutral',
    raw,
  }
}

function countMetric(key, label, icon, raw, kind, emptyNote, populatedNote, populatedTone) {
  if (isUnavailable(raw)) {
    return { ...unavailableMetric(key, label, icon, raw), value: '—' }
  }
  const count = statusRows(raw, kind).length
  let tone = count ? populatedTone : 'success'
  if (key === 'top_cpu' && count) tone = 'info'
  return {
    key,
    label,
    icon,
    value: String(count),
    note: count ? populatedNote : emptyNote,
    tone,
    raw,
  }
}

function memoryPercent(raw = '') {
  const linux = raw.match(/Mem:\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)/i)
  if (linux && Number(linux[1]) > 0) return Number(linux[2]) / Number(linux[1]) * 100
  const windows = raw.match(/total=(\d+(?:\.\d+)?)MB\s+used=(\d+(?:\.\d+)?)MB/i)
  if (windows && Number(windows[1]) > 0) return Number(windows[2]) / Number(windows[1]) * 100
  const explicit = raw.match(/(\d+(?:\.\d+)?)%/)
  return explicit ? Number(explicit[1]) : null
}

function diskPercent(raw = '') {
  let highest = null
  for (const line of meaningfulLines(raw)) {
    const explicit = line.match(/(\d+(?:\.\d+)?)%/)
    if (explicit) highest = Math.max(highest ?? 0, Number(explicit[1]))
    const windows = line.match(/total=([\d.]+)G\s+used=([\d.]+)G/i)
    if (windows && Number(windows[1]) > 0) {
      highest = Math.max(highest ?? 0, Number(windows[2]) / Number(windows[1]) * 100)
    }
  }
  return highest
}

function usageTone(percent) {
  if (percent >= 90) return 'danger'
  if (percent >= 75) return 'warning'
  return 'success'
}

function progressColor(tone) {
  return {
    success: '#209a62',
    warning: '#b7791f',
    danger: '#d14343',
    neutral: '#aebbd0',
  }[tone] || '#3979d7'
}

function meaningfulLines(raw = '') {
  return String(raw).split('\n').map((line) => line.trim()).filter(Boolean)
}

function statusRows(raw = '', kind) {
  return meaningfulLines(raw).filter((line) => {
    if (/^\(无输出\)$|^-- No entries --$/i.test(line)) return false
    const compact = line.replace(/\s+/g, ' ')
    if (/^-+(?:\s+-+)*$/.test(compact)) return false
    if (kind === 'top_cpu') {
      return !/^(Name Id CPU\(s\) Mem\(MB\)|USER PID %CPU|PID\s+)/i.test(compact)
    }
    if (kind === 'failed_units') {
      return !/^(Name DisplayName Status|UNIT LOAD ACTIVE SUB DESCRIPTION|LOAD =|ACTIVE =|SUB =|\d+ loaded units? listed)/i.test(compact)
    }
    return true
  })
}

function detailLines(raw = '') {
  if (!raw || isUnavailable(raw)) return []
  return meaningfulLines(raw).filter((line) => !/^\(无输出\)$/.test(line)).slice(0, 24)
}

function isUnavailable(raw) {
  return typeof raw !== 'string' || !raw || raw.startsWith('[采集失败]')
}

function toggleDetail(key) {
  expanded.value = expanded.value === key ? '' : key
}

const statusAgeSeconds = computed(() => {
  if (!status.value || !statusReceivedAt.value) return 0
  const base = Number(status.value.collected_ago_seconds) || 0
  return Math.max(0, base + (clock.value - statusReceivedAt.value) / 1000)
})

const ageText = computed(() => {
  const age = Math.round(statusAgeSeconds.value)
  if (age < 3) return '刚刚'
  if (age < 60) return `${age} 秒前`
  if (age < 3600) return `${Math.floor(age / 60)} 分钟前`
  return `${Math.floor(age / 3600)} 小时前`
})

const isStale = computed(() => !!status.value && statusAgeSeconds.value > 90)

const statusHeaderText = computed(() => {
  if (!status.value) return statusError.value ? '状态读取失败' : '正在读取当前主机'
  if (statusError.value) return `连接异常 · 数据为 ${ageText.value}`
  if (isStale.value) return `数据已过期 · ${ageText.value}`
  return `更新于 ${ageText.value}`
})

watch(() => props.open, (open) => {
  if (!open) expanded.value = ''
})

async function poll() {
  try {
    const response = await apiFetch('/api/status')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    status.value = await response.json()
    statusReceivedAt.value = Date.now()
    statusError.value = false
  } catch {
    statusError.value = true
  }
}

async function pollAlerts() {
  try {
    const response = await apiFetch('/api/alerts')
    if (response.ok) {
      const grouped = new Map()
      for (const alert of (await response.json()).alerts || []) {
        const key = alert.kind || alert.id
        if (!grouped.has(key)) grouped.set(key, { ...alert, duplicateIds: [alert.id] })
        else grouped.get(key).duplicateIds.push(alert.id)
      }
      alerts.value = [...grouped.values()]
    }
  } catch { /* 下轮重试 */ }
}

async function ack(alert) {
  try {
    await Promise.all((alert.duplicateIds || [alert.id]).map((id) => (
      apiFetch(`/api/alerts/${id}/ack`, { method: 'POST' })
    )))
    alerts.value = alerts.value.filter((item) => item.id !== alert.id)
  } catch { /* 下轮刷新时重试 */ }
}

onMounted(() => {
  poll()
  pollAlerts()
  timer = setInterval(poll, 30000)
  alertTimer = setInterval(pollAlerts, 30000)
  clockTimer = setInterval(() => { clock.value = Date.now() }, 1000)
})

onUnmounted(() => {
  clearInterval(timer)
  clearInterval(alertTimer)
  clearInterval(clockTimer)
})
</script>

<style scoped>
:global(.status-dialog) {
  max-width: calc(100vw - 32px);
  overflow: hidden;
}

:global(.status-dialog .el-dialog__header) {
  min-height: 68px;
  margin: 0;
  padding: 14px 52px 14px 20px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--kg-border-subtle);
}

:global(.status-dialog .el-dialog__headerbtn) {
  top: 18px;
  right: 16px;
  width: 32px;
  height: 32px;
  border-radius: var(--kg-radius-sm);
}

:global(.status-dialog .el-dialog__headerbtn:hover) {
  background: var(--kg-bg-surface-2);
}

:global(.status-dialog .el-dialog__body) {
  max-height: calc(100vh - 112px);
  padding: 0;
  overflow-y: auto;
}

.dialog-heading {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.heading-icon {
  width: 36px;
  height: 36px;
  display: grid;
  flex: none;
  place-items: center;
  border: 1px solid var(--kg-info-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-info-soft);
  color: var(--kg-info);
}

.dialog-heading h2 {
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 16px;
  font-weight: 600;
  line-height: 22px;
}

.dialog-heading p {
  margin: 1px 0 0;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
}

.dialog-heading p.is-stale { color: var(--kg-warning); }

.status-content { padding: var(--kg-space-5); }

.panel-section + .panel-section {
  margin-top: var(--kg-space-5);
  padding-top: var(--kg-space-5);
  border-top: 1px solid var(--kg-border-subtle);
}

.section-head {
  min-height: 24px;
  margin-bottom: var(--kg-space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-3);
}

.section-title {
  display: inline-flex;
  align-items: center;
  gap: var(--kg-space-2);
  color: var(--kg-text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
}

.section-title :deep(.kg-icon) { color: var(--kg-text-tertiary); }
.age,
.section-meta { color: var(--kg-text-tertiary); font-size: 12px; }

.resource-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--kg-space-3);
}

.resource-card {
  min-width: 0;
  padding: var(--kg-space-3);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-secondary);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--kg-motion-fast), background var(--kg-motion-fast),
    box-shadow var(--kg-motion-fast);
}

.resource-card:hover,
.resource-card.is-selected {
  border-color: var(--kg-border-strong);
  background: var(--kg-bg-surface-2);
  box-shadow: 0 2px 8px rgb(31 48 80 / 7%);
}

.resource-card:focus-visible,
.signal-card:focus-visible { outline: 2px solid var(--kg-focus); outline-offset: 2px; }

.resource-card-head {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--kg-space-2);
}

.metric-icon,
.signal-icon,
.stat-icon {
  display: grid;
  place-items: center;
  border-radius: var(--kg-radius-sm);
}

.metric-icon {
  width: 30px;
  height: 30px;
  background: var(--kg-bg-surface-3);
  color: var(--kg-text-tertiary);
}

.resource-card.is-success .metric-icon { background: var(--kg-success-soft); color: var(--kg-success); }
.resource-card.is-warning .metric-icon { background: var(--kg-warning-soft); color: var(--kg-warning); }
.resource-card.is-danger .metric-icon { background: var(--kg-danger-soft); color: var(--kg-danger); }

.metric-label { min-width: 0; color: var(--kg-text-secondary); font-size: 12px; }

.resource-card-head strong {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.resource-card.is-success .resource-card-head strong { color: var(--kg-success); }
.resource-card.is-warning .resource-card-head strong { color: var(--kg-warning); }
.resource-card.is-danger .resource-card-head strong { color: var(--kg-danger); }

.resource-card :deep(.el-progress) { margin: var(--kg-space-3) 0 var(--kg-space-2); }
.resource-card :deep(.el-progress-bar__outer) { background: var(--kg-bg-surface-3); }
.resource-card :deep(.el-progress.is-unavailable .el-progress-bar__inner) { opacity: 0; }

.metric-note {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-2);
  color: var(--kg-text-tertiary);
  font-size: 11px;
  line-height: 17px;
}

.metric-note > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-note .kg-icon,
.signal-chevron { flex: none; transition: transform var(--kg-motion-base) var(--kg-ease-standard); }
.metric-note .kg-icon.open,
.signal-chevron.open { transform: rotate(90deg); }

.signal-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--kg-space-3);
  margin-top: var(--kg-space-3);
}

.signal-card {
  min-width: 0;
  min-height: 64px;
  padding: var(--kg-space-2) var(--kg-space-3);
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto 13px;
  align-items: center;
  gap: var(--kg-space-2);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--kg-motion-fast), background var(--kg-motion-fast);
}

.signal-card:hover,
.signal-card.is-selected { border-color: var(--kg-border-strong); background: var(--kg-bg-surface-3); }

.signal-icon { width: 34px; height: 34px; background: var(--kg-bg-surface-1); }
.signal-card.is-info .signal-icon { background: var(--kg-info-soft); color: var(--kg-info); }
.signal-card.is-success .signal-icon { background: var(--kg-success-soft); color: var(--kg-success); }
.signal-card.is-warning .signal-icon { background: var(--kg-warning-soft); color: var(--kg-warning); }
.signal-card.is-danger .signal-icon { background: var(--kg-danger-soft); color: var(--kg-danger); }

.signal-copy { min-width: 0; display: grid; gap: 1px; }
.signal-copy > span { color: var(--kg-text-secondary); font-size: 12px; }
.signal-copy small {
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.signal-card strong {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 19px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.signal-card.is-danger strong { color: var(--kg-danger); }
.signal-card.is-warning strong { color: var(--kg-warning); }
.signal-card.is-success strong { color: var(--kg-success); }
.signal-card.is-info strong { color: var(--kg-info); }

.metric-detail {
  margin-top: var(--kg-space-3);
  overflow: hidden;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-2);
}

.detail-head {
  min-height: 38px;
  padding: 0 var(--kg-space-2) 0 var(--kg-space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.detail-head > span {
  display: inline-flex;
  align-items: center;
  gap: var(--kg-space-2);
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.detail-head button {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  cursor: pointer;
}

.detail-head button:hover { background: var(--kg-bg-surface-3); color: var(--kg-text-primary); }

.detail-lines {
  max-height: 188px;
  padding: var(--kg-space-2) var(--kg-space-3);
  overflow-y: auto;
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  line-height: 18px;
}

.detail-lines > div { padding: 2px 0; white-space: pre-wrap; word-break: break-word; }
.detail-lines > div + div { border-top: 1px solid var(--kg-border-subtle); }

.empty-detail {
  margin: 0;
  padding: var(--kg-space-4);
  color: var(--kg-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.alert-count {
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  display: inline-grid;
  place-items: center;
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-pill);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
  font-family: var(--kg-font-mono);
  font-size: 12px;
  font-weight: 600;
}

.alert-list { display: grid; gap: var(--kg-space-2); }

.alert-card {
  padding: var(--kg-space-3);
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--kg-space-3);
  border: 1px solid;
  border-radius: var(--kg-radius-md);
}

.alert-card.warning { border-color: var(--kg-warning-border); background: var(--kg-warning-soft); }
.alert-card.critical { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); }

.alert-symbol {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: var(--kg-radius-sm);
  background: rgb(255 255 255 / 70%);
  color: var(--kg-warning);
}

.alert-card.critical .alert-symbol { color: var(--kg-danger); }
.alert-copy { min-width: 0; }
.alert-head { display: flex; align-items: baseline; gap: var(--kg-space-2); }
.alert-head strong { min-width: 0; flex: 1; color: var(--kg-text-primary); font-size: 12px; }
.alert-head span {
  flex: none;
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 12px;
}

.alert-copy p {
  margin: 3px 0 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.alert-card :deep(.el-button) { align-self: center; }

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--kg-space-3);
}

.stat {
  min-width: 0;
  min-height: 62px;
  padding: var(--kg-space-2) var(--kg-space-3);
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.stat-icon { width: 32px; height: 32px; flex: none; background: var(--kg-bg-surface-2); color: var(--kg-text-tertiary); }
.stat-icon.info { background: var(--kg-info-soft); color: var(--kg-info); }
.stat-icon.success { background: var(--kg-success-soft); color: var(--kg-success); }
.stat-icon.warning { background: var(--kg-warning-soft); color: var(--kg-warning); }
.stat-icon.danger { background: var(--kg-danger-soft); color: var(--kg-danger); }

.stat > span:last-child { min-width: 0; display: grid; gap: 1px; }
.stat strong {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 22px;
}

.stat strong.info { color: var(--kg-info); }
.stat strong.success { color: var(--kg-success); }
.stat strong.warning { color: var(--kg-warning); }
.stat strong.danger { color: var(--kg-danger); }
.stat small { color: var(--kg-text-tertiary); font-size: 11px; white-space: nowrap; }

.status-error {
  min-height: 126px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--kg-space-2);
  border: 1px dashed var(--kg-border-default);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.status-error > .kg-icon { color: var(--kg-warning); }
.status-loading { padding: var(--kg-space-2) 0; }

.detail-enter-active,
.detail-leave-active { transition: opacity var(--kg-motion-fast), transform var(--kg-motion-fast); }
.detail-enter-from,
.detail-leave-to { opacity: 0; transform: translateY(-4px); }

@media (max-width: 720px) {
  :global(.status-dialog) { max-width: calc(100vw - 20px); }
  :global(.status-dialog .el-dialog__body) { max-height: calc(100vh - 96px); }
  .status-content { padding: var(--kg-space-4); }
  .resource-grid,
  .signal-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .alert-card { grid-template-columns: 30px minmax(0, 1fr); }
  .alert-card :deep(.el-button) { grid-column: 2; justify-self: start; }
}

@media (max-width: 420px) {
  :global(.status-dialog .el-dialog__header) { padding-left: var(--kg-space-4); }
  .heading-icon { display: none; }
  .stat-grid { gap: var(--kg-space-2); }
  .stat { padding: var(--kg-space-2); gap: var(--kg-space-2); }
}
</style>
