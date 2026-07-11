<template>
  <Transition name="inspector">
    <aside id="system-status-panel" ref="panel" v-show="open" class="status-panel"
           role="dialog" aria-modal="true" aria-labelledby="system-status-title"
           tabindex="-1" @keydown.esc.stop="emit('close')" @keydown.tab="trapFocus">
      <header class="inspector-head">
        <div>
          <h2 id="system-status-title">系统状态</h2>
          <p :class="{ 'is-stale': statusError || isStale }">{{ statusHeaderText }}</p>
        </div>
        <button ref="closeButton" class="close-btn" type="button" aria-label="关闭系统状态"
                title="关闭" @click="emit('close')">
          <KgIcon name="close" :size="16" />
        </button>
      </header>

      <div class="inspector-scroll">
        <section v-if="alerts.length" class="panel-section alert-section">
          <div class="section-head">
            <span class="section-title"><KgIcon name="warning" :size="15" />待处理告警</span>
            <span class="alert-count">{{ alerts.length }}</span>
          </div>
          <div v-for="a in alerts" :key="a.id" class="alert-card"
               :class="a.severity === 'critical' ? 'critical' : 'warning'">
            <div class="alert-head">
              <span class="alert-title">{{ a.title }}</span>
              <span class="alert-metric">{{ a.metric }}</span>
            </div>
            <p class="alert-msg">{{ a.message }}</p>
            <button class="ack-btn" type="button" @click.stop="ack(a)">标记已读</button>
          </div>
        </section>

        <section class="panel-section">
          <div class="section-head">
            <span class="section-title"><KgIcon name="server" :size="15" />当前主机</span>
            <span v-if="status" class="age">{{ statusError || isStale ? '等待恢复' : '30 秒刷新' }}</span>
          </div>

          <div v-if="status" class="metric-list">
            <div v-for="m in metrics" :key="m.key" class="metric"
                 :class="{ issue: ['不可用', '有失败项'].includes(m.brief), unknown: m.brief === '—' }">
              <button class="metric-toggle" type="button"
                      :aria-expanded="expanded === m.key"
                      @click="expanded = expanded === m.key ? '' : m.key">
                <KgIcon :name="metricIcon(m.key)" :size="15" />
                <span class="metric-name">{{ m.title }}</span>
                <span class="metric-value">{{ m.brief }}</span>
                <KgIcon class="metric-chevron" :class="{ open: expanded === m.key }"
                        name="chevron" :size="14" />
              </button>
              <pre v-if="expanded === m.key" class="metric-detail">{{ m.raw }}</pre>
            </div>
          </div>
          <div v-else-if="statusError" class="status-error" role="status">
            <span>暂时无法读取主机状态</span>
            <button type="button" @click="poll">重试</button>
          </div>
          <div v-else class="loading-list" aria-label="状态加载中">
            <span v-for="i in 3" :key="i" class="loading-row"></span>
          </div>
        </section>

        <section class="panel-section">
          <div class="section-head">
            <span class="section-title"><KgIcon name="activity" :size="15" />当前任务</span>
          </div>
          <div class="stat-grid">
            <div class="stat"><span class="num">{{ stats.steps }}</span><span>工具调用</span></div>
            <div class="stat"><span class="num success">{{ stats.auto }}</span><span>自动执行</span></div>
            <div class="stat"><span class="num warning">{{ stats.confirmed }}</span><span>人工确认</span></div>
            <div class="stat"><span class="num danger">{{ stats.denied }}</span><span>已拦截</span></div>
          </div>
        </section>
      </div>
    </aside>
  </Transition>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiFetch } from '../composables/useAuth.js'
import { stats } from '../composables/useChat.js'
import KgIcon from './KgIcon.vue'

const props = defineProps({ open: { type: Boolean, default: true } })
const emit = defineEmits(['close'])

const status = ref(null)
const alerts = ref([])
const expanded = ref('')
const statusError = ref(false)
const statusReceivedAt = ref(0)
const clock = ref(Date.now())
const panel = ref(null)
const closeButton = ref(null)
let timer = null
let alertTimer = null
let clockTimer = null

const TITLES = {
  uptime_load: '负载', memory: '内存', disk: '磁盘',
  top_cpu: 'CPU 进程', failed_units: '失败服务', recent_errors: '错误日志',
}

const METRIC_ICONS = {
  uptime_load: 'activity', memory: 'activity', disk: 'disk',
  top_cpu: 'cpu', failed_units: 'server', recent_errors: 'terminal',
}

const metricIcon = (key) => METRIC_ICONS[key] || 'info'

function brief(key, raw) {
  raw = typeof raw === 'string' ? raw : ''
  if (!raw) return '—'
  if (raw.startsWith('[采集失败]')) return '不可用'
  const lines = raw.split('\n').filter((l) => l.trim())
  switch (key) {
    case 'uptime_load': {
      // Linux: "load average: 0.1"  Windows: "0d 6h 9m  CPU: 4%"
      const mLinux = raw.match(/load average[s]?:\s*(.+)/)
      if (mLinux) return mLinux[1].split(',')[0].trim()
      const mWin = raw.match(/CPU:\s*(\d+)%/)
      if (mWin) return `CPU ${mWin[1]}%`
      return lines[0]?.slice(0, 20) || '—'
    }
    case 'memory': {
      // Linux: "Mem:  15585  13177  2408"  Windows: "total=15585MB used=13177MB free=2408MB"
      const mLinux = raw.match(/Mem:\s+(\d+)\s+(\d+)/)
      if (mLinux) return `${Math.round(mLinux[2] / mLinux[1] * 100)}% 已用`
      const mWin = raw.match(/total=(\d+)MB used=(\d+)MB/)
      if (mWin) return `${Math.round(+mWin[2] / +mWin[1] * 100)}% 已用`
      return '—'
    }
    case 'disk': {
      // Linux: "80%"  Windows: "used=439G free=10G" → 计算百分比
      let max = 0
      for (const l of lines) {
        const mPct = l.match(/(\d+)%/)
        if (mPct) { max = Math.max(max, +mPct[1]); continue }
        const mWin = l.match(/used=([\d.]+)G.*?total=([\d.]+)G|total=([\d.]+)G used=([\d.]+)G/)
        if (mWin) {
          const used = +(mWin[1] || mWin[4]), total = +(mWin[2] || mWin[3])
          if (total > 0) max = Math.max(max, Math.round(used / total * 100))
        }
      }
      return max > 0 ? `最高 ${max}%` : '—'
    }
    case 'failed_units':
      return statusRows(raw, 'failed_units').length ? '有失败项' : '无'
    case 'top_cpu':
      return `${statusRows(raw, 'top_cpu').length} 条`
    case 'recent_errors':
      return `${statusRows(raw, 'recent_errors').length} 条`
    default:
      return lines[0]?.slice(0, 16) || '—'
  }
}

const metrics = computed(() => {
  if (!status.value) return []
  return Object.entries(status.value.snapshot || {}).map(([key, raw]) => ({
    key, raw, title: TITLES[key] || key, brief: brief(key, raw),
  }))
})

function statusRows(raw, kind) {
  return String(raw).split('\n').map((line) => line.trim()).filter((line) => {
    if (!line) return false
    if (/^\(无输出\)$|^-- No entries --$/i.test(line)) return false
    const compact = line.replace(/\s+/g, ' ')
    if (/^-+(?:\s+-+)*$/.test(compact)) return false
    if (kind === 'top_cpu') {
      return !/^(Name Id CPU\(s\) Mem\(MB\)|USER PID %CPU|PID\s+)/i.test(compact)
    }
    return !/^(Name DisplayName Status|UNIT LOAD ACTIVE SUB DESCRIPTION|LOAD =|ACTIVE =|SUB =|\d+ loaded units? listed)/i.test(compact)
  })
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

function trapFocus(event) {
  const controls = [...(panel.value?.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ) || [])].filter((element) => !element.hidden)
  if (!controls.length) {
    event.preventDefault()
    panel.value?.focus()
    return
  }
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(() => props.open, async (open) => {
  if (!open) return
  await nextTick()
  closeButton.value?.focus()
})

async function poll() {
  try {
    const r = await apiFetch('/api/status')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    status.value = await r.json()
    statusReceivedAt.value = Date.now()
    statusError.value = false
  } catch {
    statusError.value = true
  }
}

async function pollAlerts() {
  try {
    const r = await apiFetch('/api/alerts')
    if (r.ok) {
      const grouped = new Map()
      for (const alert of (await r.json()).alerts || []) {
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
  } catch { /* 忽略 */ }
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
.status-panel {
  position: fixed;
  z-index: 40;
  top: var(--kg-pagebar-height);
  right: 0;
  bottom: 0;
  width: 336px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-left: 1px solid var(--kg-border-default);
  background: var(--kg-bg-elevated);
  color: var(--kg-text-secondary);
  box-shadow: -18px 0 36px rgb(0 0 0 / 28%);
}

.inspector-head {
  min-height: 64px;
  padding: var(--kg-space-3) var(--kg-space-3) var(--kg-space-3) var(--kg-space-4);
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--kg-border-subtle);
  background: var(--kg-bg-elevated);
}

.inspector-head h2 {
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
}

.inspector-head p {
  margin: 1px 0 0;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
}

.inspector-head p.is-stale { color: var(--kg-warning); }

.close-btn {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast);
}

.close-btn:hover {
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-primary);
}

.inspector-scroll {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding: var(--kg-space-4);
}

.panel-section + .panel-section {
  margin-top: var(--kg-space-6);
  padding-top: var(--kg-space-5);
  border-top: 1px solid var(--kg-border-subtle);
}

.section-head {
  min-height: 24px;
  margin-bottom: var(--kg-space-2);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section-title {
  display: inline-flex;
  align-items: center;
  gap: var(--kg-space-2);
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
}

.section-title :deep(.kg-icon) { color: var(--kg-text-tertiary); }
.age { color: var(--kg-text-tertiary); font-size: 12px; }

.alert-count {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
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

.alert-card {
  padding: var(--kg-space-3);
  border: 1px solid;
  border-radius: var(--kg-radius-md);
}

.alert-card + .alert-card { margin-top: var(--kg-space-2); }
.alert-card.warning { border-color: var(--kg-warning-border); background: var(--kg-warning-soft); }
.alert-card.critical { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); }

.alert-head {
  display: flex;
  align-items: baseline;
  gap: var(--kg-space-2);
}

.alert-title {
  min-width: 0;
  flex: 1;
  color: var(--kg-text-primary);
  font-size: 12px;
  font-weight: 600;
}

.alert-metric {
  flex: none;
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 12px;
}

.alert-msg {
  margin: var(--kg-space-1) 0 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.ack-btn {
  height: 28px;
  margin-top: var(--kg-space-2);
  padding: 0 var(--kg-space-2);
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.ack-btn:hover {
  border-color: var(--kg-border-strong);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-primary);
}

.metric-list { display: grid; gap: var(--kg-space-1); }

.metric {
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  transition: border-color var(--kg-motion-fast), background var(--kg-motion-fast);
}

.metric:hover { border-color: var(--kg-border-default); }
.metric.issue .metric-value { color: var(--kg-danger); }
.metric.unknown .metric-value { color: var(--kg-warning); }

.metric-toggle {
  width: 100%;
  min-height: 38px;
  padding: 0 var(--kg-space-2);
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  border: 0;
  background: transparent;
  color: var(--kg-text-tertiary);
  cursor: pointer;
  text-align: left;
}

.metric-toggle:focus-visible { outline-offset: -2px; }

.metric-name {
  min-width: 0;
  flex: 1;
  color: var(--kg-text-secondary);
  font-size: 12px;
}

.metric-value {
  color: var(--kg-info);
  font-family: var(--kg-font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.metric-chevron {
  transition: transform var(--kg-motion-base) var(--kg-ease-standard);
}

.metric-chevron.open { transform: rotate(90deg); }

.metric-detail {
  max-height: 220px;
  margin: 0 var(--kg-space-2) var(--kg-space-2);
  padding: var(--kg-space-2);
  overflow-y: auto;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-bg-code);
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  line-height: 17px;
  white-space: pre-wrap;
  word-break: break-all;
}

.loading-list { display: grid; gap: var(--kg-space-1); }
.loading-row {
  height: 38px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.status-error {
  min-height: 96px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: var(--kg-space-2);
  border: 1px dashed var(--kg-border-default);
  border-radius: var(--kg-radius-md);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.status-error button {
  height: 28px;
  padding: 0 var(--kg-space-3);
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  cursor: pointer;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--kg-space-2);
}

.stat {
  min-width: 0;
  padding: var(--kg-space-3);
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.num {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 20px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 26px;
}

.num.success { color: var(--kg-success); }
.num.warning { color: var(--kg-warning); }
.num.danger { color: var(--kg-danger); }

.inspector-enter-active,
.inspector-leave-active {
  transition: opacity var(--kg-motion-base) var(--kg-ease-standard),
    transform var(--kg-motion-base) var(--kg-ease-standard);
}

.inspector-enter-from,
.inspector-leave-to {
  opacity: 0;
  transform: translateX(18px);
}

@media (max-width: 1080px) {
  .status-panel { width: 320px; }
}
</style>
