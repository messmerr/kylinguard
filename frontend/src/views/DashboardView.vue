<template>
  <div class="kg-page dashboard-page">
    <div class="kg-page-inner dashboard-inner">
      <header class="page-head">
        <div>
          <p class="page-description">查看当前系统状态和累计安全活动。</p>
        </div>
        <div class="refresh-state">
          <span v-if="status" class="kg-meta">{{ ageText }}更新 · 每 30 秒刷新</span>
          <el-button :loading="refreshing" @click="poll">
            <KgIcon v-if="!refreshing" name="refresh" :size="15" />
            刷新
          </el-button>
        </div>
      </header>

      <div class="overview-grid">
        <section class="overview-panel health-panel">
          <div class="section-head">
            <div>
              <h2 class="kg-section-title">系统状态</h2>
              <span class="kg-meta">来自最近一次状态快照</span>
            </div>
            <span v-if="status" class="collection-state" :class="collectionState.className">
              <span class="state-dot"></span>
              {{ collectionState.label }}
            </span>
          </div>

          <div v-if="status" class="health-list">
            <div v-for="metric in healthMetrics" :key="metric.key" class="health-row">
              <span class="health-icon" :class="metric.tone">
                <KgIcon :name="metric.icon" :size="16" />
              </span>
              <div class="health-main">
                <div class="health-labels">
                  <span>{{ metric.label }}</span>
                  <strong :class="metric.tone">{{ metric.value }}</strong>
                </div>
                <div v-if="metric.percent != null" class="metric-track" aria-hidden="true">
                  <span
                    class="metric-fill"
                    :class="metric.tone"
                    :style="{ width: `${metric.percent}%` }"
                  ></span>
                </div>
                <span v-else class="metric-note">{{ metric.note }}</span>
              </div>
            </div>
          </div>

          <div v-else class="panel-loading">
            <span class="kg-spinner"></span>
            正在读取系统状态
          </div>
        </section>

        <section class="overview-panel activity-panel">
          <div class="section-head">
            <div>
              <h2 class="kg-section-title">安全活动</h2>
              <span class="kg-meta">累计记录</span>
            </div>
            <span v-if="stats" class="event-total">{{ stats.total_events || 0 }} 条审计事件</span>
          </div>

          <template v-if="stats">
            <div class="session-total">
              <strong>{{ stats.sessions || 0 }}</strong>
              <span>累计任务</span>
            </div>
            <dl class="activity-list">
              <div v-for="item in activityStats" :key="item.label" class="activity-row">
                <dt>
                  <span class="activity-dot" :class="item.tone"></span>
                  {{ item.label }}
                </dt>
                <dd :class="item.tone">{{ item.value }}</dd>
              </div>
            </dl>
          </template>

          <div v-else class="panel-loading">
            <span class="kg-spinner"></span>
            正在读取活动统计
          </div>
        </section>
      </div>

      <section class="details-section">
        <div class="details-head">
          <div>
            <h2 class="kg-section-title">详细状态</h2>
            <p>展开查看采集器返回的原始信息。</p>
          </div>
        </div>

        <el-collapse v-if="rawMetrics.length" v-model="openDetails" class="status-collapse">
          <el-collapse-item v-for="metric in rawMetrics" :key="metric.key" :name="metric.key">
            <template #title>
              <div class="collapse-title">
                <span>{{ metric.title }}</span>
                <span v-if="metric.unavailable" class="unavailable">采集失败</span>
                <span v-else class="collapse-summary">{{ metric.summary }}</span>
              </div>
            </template>
            <pre class="raw-output">{{ metric.raw }}</pre>
          </el-collapse-item>
        </el-collapse>

        <div v-else class="kg-empty details-empty">
          <KgIcon name="server" :size="22" />
          <strong>详细状态加载中</strong>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useAuth.js'

const stats = ref(null)
const status = ref(null)
const refreshing = ref(false)
const openDetails = ref([])
let timer = null

const TITLES = {
  uptime_load: '运行时长与负载',
  memory: '内存',
  disk: '磁盘',
  top_cpu: 'CPU 占用最高进程',
  failed_units: '失败服务',
  recent_errors: '近期错误日志',
}

const ageText = computed(() => {
  const age = status.value?.collected_ago_seconds ?? 0
  return age < 3 ? '刚刚' : `${Math.round(age)} 秒前`
})

const collectionState = computed(() => {
  const values = Object.values(status.value?.snapshot || {})
  const failed = values.filter(raw => isUnavailable(raw)).length
  if (!values.length) return { label: '等待采集', className: 'is-neutral' }
  if (failed) return { label: `${failed} 项不可用`, className: 'is-warning' }
  return { label: '采集正常', className: 'is-ok' }
})

const healthMetrics = computed(() => {
  const snapshot = status.value?.snapshot || {}
  return [
    loadMetric(snapshot.uptime_load),
    percentMetric('memory', '内存', 'server', memoryPercent(snapshot.memory), snapshot.memory),
    percentMetric('disk', '磁盘最高', 'disk', diskPercent(snapshot.disk), snapshot.disk),
    failedMetric(snapshot.failed_units),
  ]
})

const activityStats = computed(() => [
  { label: '已执行', value: stats.value?.by_type?.execution || 0, tone: 'info' },
  { label: '已阻止', value: stats.value?.denied || 0, tone: 'danger' },
  { label: '人工批准', value: stats.value?.confirm_approved || 0, tone: 'success' },
  { label: '人工拒绝', value: stats.value?.confirm_rejected || 0, tone: 'warning' },
])

const rawMetrics = computed(() => {
  const snapshot = status.value?.snapshot || {}
  return Object.entries(snapshot).map(([key, raw]) => ({
    key,
    raw,
    title: TITLES[key] || key,
    unavailable: isUnavailable(raw),
    summary: metricSummary(key, raw),
  }))
})

function loadMetric(raw = '') {
  if (!raw || isUnavailable(raw)) {
    return unavailableMetric('uptime_load', '系统负载', 'activity')
  }
  const cpu = raw.match(/CPU:\s*(\d+(?:\.\d+)?)%/i)
  if (cpu) return percentMetric('uptime_load', 'CPU', 'cpu', Number(cpu[1]), raw)

  const load = raw.match(/load average[s]?:\s*([\d.]+)/i)
  if (load) {
    return {
      key: 'uptime_load', label: '系统负载', icon: 'activity',
      value: load[1], percent: null, note: '最近 1 分钟平均负载', tone: 'neutral',
    }
  }

  return {
    key: 'uptime_load', label: '系统负载', icon: 'activity',
    value: '已采集', percent: null, note: firstLine(raw), tone: 'neutral',
  }
}

function percentMetric(key, label, icon, percent, raw = '') {
  if (percent == null || Number.isNaN(percent) || isUnavailable(raw)) {
    return unavailableMetric(key, label, icon)
  }
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)))
  return {
    key, label, icon, value: `${safePercent}%`, percent: safePercent,
    note: '', tone: usageTone(safePercent),
  }
}

function failedMetric(raw = '') {
  if (!raw || isUnavailable(raw)) return unavailableMetric('failed_units', '失败服务', 'warning')
  const count = statusRows(raw, 'failed_units').length
  return {
    key: 'failed_units', label: '失败服务', icon: count ? 'warning' : 'check',
    value: String(count), percent: null,
    note: count ? '存在需要处理的服务' : '未发现失败服务',
    tone: count ? 'danger' : 'success',
  }
}

function unavailableMetric(key, label, icon) {
  return { key, label, icon, value: '不可用', percent: null, note: '本次采集未返回有效数据', tone: 'disabled' }
}

function memoryPercent(raw = '') {
  if (!raw) return null
  const linux = raw.match(/Mem:\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)/i)
  if (linux && Number(linux[1]) > 0) return Number(linux[2]) / Number(linux[1]) * 100
  const windows = raw.match(/total=(\d+(?:\.\d+)?)MB\s+used=(\d+(?:\.\d+)?)MB/i)
  if (windows && Number(windows[1]) > 0) return Number(windows[2]) / Number(windows[1]) * 100
  const explicit = raw.match(/(\d+(?:\.\d+)?)%/)
  return explicit ? Number(explicit[1]) : null
}

function diskPercent(raw = '') {
  if (!raw) return null
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
  return 'accent'
}

function metricSummary(key, raw = '') {
  if (isUnavailable(raw)) return '不可用'
  if (key === 'memory') {
    const value = memoryPercent(raw)
    return value == null ? '已采集' : `${Math.round(value)}% 已用`
  }
  if (key === 'disk') {
    const value = diskPercent(raw)
    return value == null ? '已采集' : `最高 ${Math.round(value)}%`
  }
  if (key === 'failed_units') {
    return failedMetric(raw).note
  }
  if (key === 'top_cpu') return `${statusRows(raw, 'top_cpu').length} 条`
  if (key === 'recent_errors') return `${statusRows(raw, 'recent_errors').length} 条`
  return firstLine(raw)
}

function firstLine(raw = '') {
  return meaningfulLines(raw)[0]?.slice(0, 56) || '已采集'
}

function meaningfulLines(raw = '') {
  return String(raw).split('\n').map(line => line.trim()).filter(Boolean)
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

function isUnavailable(raw) {
  return typeof raw !== 'string' || raw.startsWith('[采集失败]')
}

async function poll() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const [statusResponse, statsResponse] = await Promise.all([
      apiFetch('/api/status'),
      apiFetch('/api/stats'),
    ])
    if (statusResponse.ok) status.value = await statusResponse.json()
    if (statsResponse.ok) stats.value = await statsResponse.json()
  } catch {
    // 保留上一次成功结果，等待下一轮刷新。
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  poll()
  timer = setInterval(poll, 30000)
})

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.dashboard-inner { width: min(100%, 1120px); }

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-6);
}

.page-description {
  margin: 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
}

.refresh-state {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.refresh-state :deep(.el-button) { gap: 7px; }

.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.9fr) minmax(290px, 1fr);
  gap: var(--kg-space-4);
  margin-top: var(--kg-space-6);
}

.overview-panel {
  min-width: 0;
  padding: var(--kg-space-5);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-4);
}

.section-head .kg-meta { display: block; margin-top: 2px; }

.collection-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.collection-state.is-ok { color: var(--kg-success); }
.collection-state.is-warning { color: var(--kg-warning); }

.state-dot,
.activity-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.health-list { display: grid; gap: 17px; margin-top: var(--kg-space-5); }

.health-row {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  align-items: center;
  gap: var(--kg-space-3);
}

.health-icon {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
}

.health-icon.accent { color: var(--kg-accent); }
.health-icon.info { color: var(--kg-info); }
.health-icon.success { color: var(--kg-success); }
.health-icon.warning { color: var(--kg-warning); }
.health-icon.danger { color: var(--kg-danger); }
.health-icon.disabled { color: var(--kg-text-disabled); }

.health-main { min-width: 0; }

.health-labels {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--kg-space-3);
  margin-bottom: 7px;
  color: var(--kg-text-secondary);
  font-size: 13px;
}

.health-labels strong {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 12px;
  font-weight: 600;
}

.health-labels strong.warning { color: var(--kg-warning); }
.health-labels strong.danger { color: var(--kg-danger); }
.health-labels strong.success { color: var(--kg-success); }
.health-labels strong.disabled { color: var(--kg-text-disabled); }

.metric-track {
  height: 5px;
  overflow: hidden;
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-surface-3);
}

.metric-fill {
  display: block;
  min-width: 2px;
  height: 100%;
  border-radius: inherit;
  background: var(--kg-accent);
  transition: width var(--kg-motion-slow) var(--kg-ease-standard);
}

.metric-fill.warning { background: var(--kg-warning); }
.metric-fill.danger { background: var(--kg-danger); }
.metric-fill.info { background: var(--kg-info); }

.metric-note {
  display: block;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-total {
  color: var(--kg-text-tertiary);
  font-size: 11px;
  white-space: nowrap;
}

.session-total {
  display: flex;
  align-items: baseline;
  gap: var(--kg-space-2);
  margin: var(--kg-space-5) 0 var(--kg-space-4);
}

.session-total strong {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 30px;
  font-weight: 650;
  line-height: 1;
}

.session-total span { color: var(--kg-text-tertiary); font-size: 12px; }

.activity-list { display: grid; gap: 0; margin: 0; }

.activity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 34px;
  border-top: 1px solid var(--kg-border-subtle);
}

.activity-row dt {
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  color: var(--kg-text-secondary);
  font-size: 12px;
}

.activity-row dd {
  margin: 0;
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 13px;
  font-weight: 600;
}

.activity-dot.info { color: var(--kg-info); }
.activity-dot.danger { color: var(--kg-danger); }
.activity-dot.success { color: var(--kg-success); }
.activity-dot.warning { color: var(--kg-warning); }
.activity-row dd.danger { color: var(--kg-danger); }
.activity-row dd.warning { color: var(--kg-warning); }

.panel-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--kg-space-2);
  min-height: 190px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.details-section { margin-top: var(--kg-space-8); }

.details-head p {
  margin: 3px 0 var(--kg-space-3);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.status-collapse :deep(.el-collapse-item__header) { padding: 0 var(--kg-space-2); }
.status-collapse :deep(.el-collapse-item__content) { padding-bottom: var(--kg-space-3); }

.collapse-title {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  padding-right: var(--kg-space-3);
}

.collapse-title > span:first-child { color: var(--kg-text-secondary); }

.collapse-summary,
.unavailable {
  margin-left: auto;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  font-weight: 400;
}

.unavailable { color: var(--kg-danger); }

.raw-output {
  max-height: 280px;
  margin: 0;
  padding: var(--kg-space-3);
  overflow: auto;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-bg-code);
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.details-empty { min-height: 120px; }

@media (max-width: 1080px) {
  .overview-grid { grid-template-columns: 1fr; }
  .activity-list { grid-template-columns: 1fr 1fr; column-gap: var(--kg-space-5); }
}
</style>
