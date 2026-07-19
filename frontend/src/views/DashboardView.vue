<template>
  <div class="kg-page dashboard-page">
    <div class="kg-page-inner dashboard-inner">
      <div v-if="dashboardError && hasDashboardData" class="dashboard-notice" role="alert">
        <KgIcon name="warning" :size="15" />
        <span><strong>部分信息未能刷新</strong>{{ dashboardError }}；{{ dashboardNoticeDetail }}</span>
        <el-button text :loading="refreshing" @click="poll({ forceStatus: true })">重试</el-button>
      </div>

      <div class="dashboard-tabs-shell">
        <div class="refresh-state">
          <span v-if="status" class="kg-meta">{{ ageText }}更新</span>
          <el-button :loading="refreshing" @click="poll({ forceStatus: true })">
            <KgIcon v-if="!refreshing" name="refresh" :size="15" />
            刷新
          </el-button>
        </div>

      <el-tabs v-model="activeTab" class="dashboard-tabs">
        <el-tab-pane label="运行概览" name="overview">
          <div v-if="initialLoading" class="dashboard-state" role="status" aria-live="polite">
            <span class="sr-only">正在汇总运行概览，请稍候</span>
            <div class="skeleton-strip" aria-hidden="true">
              <span v-for="i in 4" :key="i" class="kg-shimmer skeleton-cell"></span>
            </div>
            <div class="skeleton-grid" aria-hidden="true">
              <span class="kg-shimmer skeleton-panel"></span>
              <span class="kg-shimmer skeleton-panel is-small"></span>
            </div>
          </div>

          <div v-else-if="dashboardError && !hasDashboardData" class="dashboard-state is-error" role="alert">
            <span class="state-icon"><KgIcon name="warning" :size="20" /></span>
            <div><strong>运行概览暂不可用</strong><span>{{ dashboardError }}</span></div>
            <el-button :loading="refreshing" @click="poll">重新加载</el-button>
          </div>

          <template v-else>
          <section class="summary-strip" aria-label="运行概览">
            <div v-for="item in summaryStats" :key="item.label" class="summary-item kg-enter">
              <span class="kg-eyebrow">{{ item.label }}</span>
              <strong :class="item.tone"><CountUp :value="item.value" /></strong>
              <small>{{ item.note }}</small>
            </div>
          </section>

          <div class="overview-grid">
            <section class="overview-panel health-panel kg-enter" :style="{ '--kg-enter-delay': '280ms' }">
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

              <div v-if="status" class="platform-identity" :class="`is-${platformSummary.tone}`">
                <span class="platform-icon"><KgIcon name="server" :size="18" /></span>
                <div class="platform-copy">
                  <span>目标环境感知</span>
                  <strong :title="platformSummary.title">{{ platformSummary.title }}</strong>
                  <small :title="platformSummary.detail">{{ platformSummary.detail }}</small>
                </div>
                <span class="platform-match">{{ platformSummary.badge }}</span>
              </div>

              <div v-if="status" class="health-list">
                <div v-for="metric in healthMetrics" :key="metric.key" class="health-metric">
                  <div class="metric-ring" :style="ringStyle(metric)" aria-hidden="true">
                    <div class="metric-ring-core">
                      <strong :class="metric.tone">{{ metric.value }}</strong>
                      <span>{{ metric.label }}</span>
                    </div>
                  </div>
                  <span class="metric-accessible">{{ metric.label }}：{{ metric.value }}</span>
                  <span class="metric-note">{{ metric.note || metric.label }}</span>
                </div>
              </div>

              <div v-if="status" class="capability-summary">
                <div class="capability-head">
                  <strong>已启用能力</strong>
                  <span>系统自带与自定义</span>
                </div>
                <div class="capability-row">
                  <span class="capability-label">
                    <KgIcon name="server" :size="14" />MCP
                    <b>{{ enabledMcpServers.length }}</b>
                  </span>
                  <div class="capability-items">
                    <span
                      v-for="server in enabledMcpServers"
                      :key="server.id"
                      class="capability-chip"
                      :class="{ unavailable: !server.available }"
                      :title="`${server.source === 'builtin' ? '系统自带' : '第三方'} · ${server.toolCount} 个工具${server.available ? '' : ' · 当前不可用'}`"
                    >
                      {{ server.name }}
                      <small v-if="server.source !== 'builtin'">第三方</small>
                    </span>
                    <span v-if="!enabledMcpServers.length" class="capability-empty">暂无已启用 MCP</span>
                  </div>
                </div>
                <div class="capability-row">
                  <span class="capability-label">
                    <KgIcon name="skill" :size="14" />Skill
                    <b>{{ enabledSkillSummaries.length }}</b>
                  </span>
                  <div class="capability-items">
                    <span
                      v-for="skill in enabledSkillSummaries"
                      :key="skill.id"
                      class="capability-chip"
                      :class="{ unavailable: !skill.available }"
                      :title="`${skill.source === 'builtin' ? '系统自带' : '自定义'}${skill.available ? '' : ' · 当前不可用'}`"
                    >
                      {{ skill.name }}
                      <small v-if="skill.source !== 'builtin'">自定义</small>
                    </span>
                    <span v-if="!enabledSkillSummaries.length" class="capability-empty">暂无已启用 Skill</span>
                  </div>
                </div>
              </div>

              <div v-else class="panel-loading" :class="{ 'is-error': statusError }" :role="statusError ? 'alert' : 'status'">
                <KgIcon v-if="statusError" name="warning" :size="17" />
                <span v-else-if="!initialLoadComplete || refreshing" class="kg-spinner" aria-hidden="true"></span>
                <KgIcon v-else name="server" :size="17" />
                {{ statusError
                  ? '系统状态本次未能刷新'
                  : !initialLoadComplete || refreshing ? '正在读取系统状态' : '暂无系统状态' }}
              </div>
            </section>

            <section class="overview-panel activity-panel kg-enter" :style="{ '--kg-enter-delay': '350ms' }">
              <template v-if="stats">
                <EChartCanvas :option="activityChartOption" title="安全活动分布" :height="250" embedded />
                <dl class="activity-list">
                  <div v-for="item in activityStats" :key="item.label" class="activity-row">
                    <dt>
                      <span class="activity-dot" :class="item.tone"></span>
                      {{ item.label }}
                    </dt>
                    <dd :class="item.tone"><CountUp :value="item.value" /></dd>
                  </div>
                </dl>
              </template>

              <div v-else class="panel-loading" :class="{ 'is-error': statsError }" :role="statsError ? 'alert' : 'status'">
                <KgIcon v-if="statsError" name="warning" :size="17" />
                <span v-else-if="!initialLoadComplete || refreshing" class="kg-spinner" aria-hidden="true"></span>
                <KgIcon v-else name="activity" :size="17" />
                {{ statsError
                  ? '活动统计本次未能刷新'
                  : !initialLoadComplete || refreshing ? '正在读取活动统计' : '暂无活动统计' }}
              </div>
            </section>
          </div>
          </template>
        </el-tab-pane>

        <el-tab-pane label="详细状态" name="details">
          <section class="details-section overview-panel">
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
                    <span v-if="metric.unavailable" class="unavailable">{{ metric.unavailableLabel }}</span>
                    <span v-else class="collapse-summary">{{ metric.summary }}</span>
                  </div>
                </template>
                <pre
                  class="raw-output"
                  :class="{ 'is-memory-table': metric.key === 'memory' }"
                >{{ metric.raw }}</pre>
              </el-collapse-item>
            </el-collapse>

            <div v-else class="kg-empty details-empty" :class="{ 'is-error': statusError }">
              <span v-if="!initialLoadComplete || refreshing" class="kg-spinner" aria-hidden="true"></span>
              <KgIcon v-else :name="statusError ? 'warning' : 'server'" :size="22" />
              <strong>{{ !initialLoadComplete || refreshing
                ? '正在汇总详细状态'
                : statusError ? '详细状态暂不可用' : '暂无详细状态' }}</strong>
              <span v-if="statusError">{{ statusError }}</span>
              <el-button v-if="statusError" :loading="refreshing" @click="poll">重试</el-button>
            </div>
          </section>
        </el-tab-pane>
      </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import CountUp from '../components/CountUp.vue'
import EChartCanvas from '../components/EChartCanvas.vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useApi.js'
import {
  pendingAlerts as alerts,
  pendingAlertsError,
  pendingAlertsLoaded,
  refreshPendingAlerts,
} from '../composables/useAlerts.js'
import {
  refreshSystemStatus,
  systemStatus as status,
  systemStatusAgeText as ageText,
  systemStatusError as sharedSystemStatusError,
} from '../composables/useSystemStatus.js'
import {
  enabledMcpServers,
  extensionSkills,
  loadExtensions,
} from '../composables/useExtensions.js'
import {
  cpuUsagePercent,
  diskUsagePercent as diskPercent,
  formatMemoryTable,
  isMetricUnavailable as isUnavailable,
  isMetricUnsupported,
  loadAverage,
  memoryUsagePercent as memoryPercent,
  platformIdentity,
} from '../utils/systemMetrics.js'

const stats = ref(null)
const refreshing = ref(false)
const endpointErrors = ref({})
const retainedErrorKeys = ref([])
const initialLoadComplete = ref(false)
const openDetails = ref([])
const activeTab = ref('overview')
let timer = null
const endpointLoaded = {
  status: false,
  stats: false,
  alerts: false,
  extensions: false,
}

const hasDashboardData = computed(() => Boolean(
  status.value || stats.value || pendingAlertsLoaded.value,
))
const initialLoading = computed(() => (
  (!initialLoadComplete.value || refreshing.value) && !hasDashboardData.value
))
const dashboardError = computed(() => {
  const errors = { ...endpointErrors.value }
  if (sharedSystemStatusError.value) errors.status = sharedSystemStatusError.value
  return Object.values(errors).filter(Boolean).join('；')
})
const statusError = computed(() => sharedSystemStatusError.value || endpointErrors.value.status || '')
const statsError = computed(() => endpointErrors.value.stats || '')
const alertError = computed(() => endpointErrors.value.alerts || '')
const dashboardNoticeDetail = computed(() => {
  const failedCount = Object.keys(endpointErrors.value).length
  const retainedCount = retainedErrorKeys.value.length
  if (!failedCount || !retainedCount) return '未成功读取的项目暂不显示，请稍后重试。'
  if (retainedCount === failedCount) return '相关项目继续显示最近一次成功结果。'
  return '已有结果的项目继续显示最近一次成功结果，首次读取失败的项目暂不显示。'
})

const TONE_COLORS = Object.freeze({
  accent: '#175cff',
  info: '#3979d7',
  success: '#209a62',
  warning: '#b7791f',
  danger: '#d14343',
  disabled: '#c9d2e1',
  neutral: '#7b879d',
})

const TITLES = {
  platform_identity: '银河麒麟环境身份',
  uptime_load: '运行时长与负载',
  memory: '内存',
  disk: '磁盘',
  top_cpu: 'CPU 占用最高进程',
  failed_units: '失败服务',
  recent_errors: '近期错误日志',
}

const collectionState = computed(() => {
  const values = Object.values(status.value?.snapshot || {})
  const failed = values.filter(raw => isUnavailable(raw)).length
  if (!values.length) return { label: '等待采集', className: 'is-neutral' }
  if (failed) return { label: `${failed} 项不可用`, className: 'is-warning' }
  return { label: '采集正常', className: 'is-ok' }
})

const platformProfile = computed(() => (
  platformIdentity(status.value?.snapshot?.platform_identity)
))

const platformSummary = computed(() => {
  const profile = platformProfile.value
  if (!profile) return {
    title: '环境身份尚未确认', detail: '等待版本、架构和内核证据',
    badge: '未确认', tone: 'warning',
  }
  const targetStatus = profile.contest_target?.status
  const detected = Boolean(profile.kylin?.detected)
  const title = profile.os?.pretty_name
    || (detected ? '银河麒麟操作系统' : profile.os?.name)
    || profile.platform || '未知系统'
  const version = profile.kylin?.version || profile.os?.version_id || '版本未确认'
  const architecture = profile.architecture?.normalized || '架构未确认'
  const kernel = profile.kernel?.release ? ` · 内核 ${profile.kernel.release}` : ''
  return {
    title,
    detail: `${version} · ${architecture}${kernel}`,
    badge: targetStatus === 'matched' ? '赛题环境匹配'
      : targetStatus === 'partial' ? '部分证据待确认' : '非目标环境',
    tone: targetStatus === 'matched' ? 'success'
      : targetStatus === 'partial' ? 'warning' : 'neutral',
  }
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

const summaryStats = computed(() => [
  {
    label: '待处理告警', value: pendingAlertsLoaded.value ? activeAlertCount.value : '—',
    note: alertSummary.value.note,
    tone: alertSummary.value.tone,
  },
  { label: '累计任务', value: stats.value ? stats.value.sessions || 0 : '—', note: '历史会话', tone: 'accent' },
  { label: '审计事件', value: stats.value ? stats.value.total_events || 0 : '—', note: '哈希链记录', tone: 'info' },
  { label: '风险阻止', value: stats.value ? stats.value.denied || 0 : '—', note: '策略命中', tone: 'danger' },
])

const activeAlertCount = computed(() => alerts.value?.length || 0)
const enabledSkillSummaries = computed(() => (
  extensionSkills.value.filter(skill => skill.enabled)
))

const alertSummary = computed(() => {
  if (!pendingAlertsLoaded.value) return {
    note: alertError.value
      ? '告警状态未同步'
      : initialLoadComplete.value ? '暂无告警状态' : '正在读取当前风险',
    tone: alertError.value ? 'warning' : 'info',
  }
  const critical = alerts.value.filter(alert => alert.severity === 'critical').length
  const stale = alertError.value ? ' · 刷新未完成' : ''
  if (critical) return { note: `${critical} 条严重风险待处理${stale}`, tone: 'danger' }
  if (alerts.value.length) return {
    note: `${alerts.value.length} 条风险需要关注${stale}`, tone: 'warning',
  }
  if (alertError.value) return { note: '显示最近一次结果 · 刷新未完成', tone: 'warning' }
  return { note: '当前没有待处理风险', tone: 'success' }
})

const activityChartOption = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { bottom: 4, icon: 'circle', itemWidth: 8, itemHeight: 8 },
  series: [{
    type: 'pie', radius: ['52%', '74%'], center: ['50%', '43%'],
    label: { show: false },
    emphasis: {
      label: { show: true, fontSize: 13, fontWeight: 600 },
      // 悬停扇区轻微放大，配合 expo 缓动的回弹感
      scaleSize: 8,
    },
    animationType: 'scale',
    animationEasing: 'exponentialOut',
    animationDelay: (idx) => idx * 100,
    data: activityStats.value.map(item => ({
      name: item.label,
      value: item.value,
      itemStyle: { color: TONE_COLORS[item.tone] },
    })),
  }],
  graphic: [{
    type: 'text', left: 'center', top: '36%',
    style: {
      text: `${stats.value?.sessions || 0}\n任务`, textAlign: 'center',
      fill: '#172033', font: '600 16px Inter, "Noto Sans SC", sans-serif', lineHeight: 24,
    },
  }],
}))

function ringStyle(metric) {
  const percent = metric.percent ?? (metric.tone === 'success' ? 100 : 12)
  return {
    '--kg-ring-p': `${percent}%`,
    '--kg-ring-c': TONE_COLORS[metric.tone] || TONE_COLORS.accent,
  }
}

const rawMetrics = computed(() => {
  const snapshot = status.value?.snapshot || {}
  return Object.entries(snapshot).map(([key, raw]) => ({
    key,
    raw: key === 'memory' ? formatMemoryTable(raw) : raw,
    title: TITLES[key] || key,
    unavailable: isUnavailable(raw),
    unavailableLabel: isMetricUnsupported(raw) ? '当前平台不支持' : '采集失败',
    summary: metricSummary(key, raw),
  }))
})

function loadMetric(raw = '') {
  if (!raw || isUnavailable(raw)) {
    return unavailableMetric('uptime_load', '系统负载', 'activity', raw)
  }
  const cpu = cpuUsagePercent(raw)
  if (cpu != null) return percentMetric('uptime_load', 'CPU', 'cpu', cpu, raw)

  const load = loadAverage(raw)
  if (load != null) {
    return {
      key: 'uptime_load', label: '系统负载', icon: 'activity',
      value: String(load), percent: null, tone: 'neutral',
      note: '最近 1 分钟平均负载',
    }
  }

  return {
    key: 'uptime_load', label: '系统负载', icon: 'activity',
    value: '已采集', percent: null, note: firstLine(raw), tone: 'neutral',
  }
}

function percentMetric(key, label, icon, percent, raw = '') {
  if (percent == null || Number.isNaN(percent) || isUnavailable(raw)) {
    return unavailableMetric(key, label, icon, raw)
  }
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)))
  return {
    key, label, icon, value: `${safePercent}%`, percent: safePercent,
    note: '', tone: usageTone(safePercent),
  }
}

function failedMetric(raw = '') {
  if (!raw || isUnavailable(raw)) return unavailableMetric('failed_units', '失败服务', 'warning', raw)
  const count = statusRows(raw, 'failed_units').length
  return {
    key: 'failed_units', label: '失败服务', icon: count ? 'warning' : 'check',
    value: String(count), percent: null,
    note: count ? '存在需要处理的服务' : '未发现失败服务',
    tone: count ? 'danger' : 'success',
  }
}

function unavailableMetric(key, label, icon, raw = '') {
  return {
    key, label, icon, value: '不可用', percent: null,
    note: isMetricUnsupported(raw) ? '当前平台不支持' : '本次采集未返回有效数据',
    tone: 'disabled',
  }
}

function usageTone(percent) {
  if (percent >= 90) return 'danger'
  if (percent >= 75) return 'warning'
  return 'success'
}

function metricSummary(key, raw = '') {
  if (isUnavailable(raw)) return '不可用'
  if (key === 'uptime_load') {
    const metric = loadMetric(raw)
    return metric.percent == null ? metric.value : `${metric.label} ${metric.value}`
  }
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

async function poll({ includeStatus = true, forceStatus = false } = {}) {
  if (refreshing.value) return
  refreshing.value = true
  const previouslyLoaded = {
    ...endpointLoaded,
    alerts: endpointLoaded.alerts || pendingAlertsLoaded.value,
    extensions: endpointLoaded.extensions
      || Boolean(enabledMcpServers.value.length || extensionSkills.value.length),
  }
  endpointErrors.value = {}
  retainedErrorKeys.value = []
  const requests = await Promise.allSettled([
    includeStatus
      ? refreshSystemStatus({ force: forceStatus })
      : Promise.resolve(status.value),
    apiFetch('/api/stats'),
    refreshPendingAlerts(),
    loadExtensions(),
  ])
  const errors = {}

  async function readResponse(result, key, label) {
    if (result.status === 'rejected') {
      errors[key] = `${label}连接失败`
      return null
    }
    if (!result.value.ok) {
      errors[key] = `${label}返回 HTTP ${result.value.status}`
      return null
    }
    try {
      const body = await result.value.json()
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        errors[key] = `${label}响应格式不正确`
        return null
      }
      return body
    } catch {
      errors[key] = `${label}响应无法解析`
      return null
    }
  }

  try {
    const statsBody = await readResponse(requests[1], 'stats', '活动统计')
    if (requests[0].status === 'rejected') {
      errors.status = sharedSystemStatusError.value || '系统状态连接失败'
    } else {
      endpointLoaded.status = true
    }
    if (requests[2].status === 'rejected') {
      errors.alerts = pendingAlertsError.value || '告警连接失败'
    } else {
      endpointLoaded.alerts = true
    }
    if (requests[3].status === 'rejected') errors.extensions = '扩展能力同步失败'
    else endpointLoaded.extensions = true

    if (statsBody) {
      stats.value = statsBody
      endpointLoaded.stats = true
    }
    endpointErrors.value = errors
    retainedErrorKeys.value = Object.keys(errors).filter(key => previouslyLoaded[key])
  } finally {
    initialLoadComplete.value = true
    refreshing.value = false
  }
}

onMounted(() => {
  poll()
  timer = setInterval(() => poll({ includeStatus: false }), 30000)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.dashboard-inner { width: 100%; }

.dashboard-tabs-shell { position: relative; }

/* 首屏加载骨架屏：轮廓与真实布局一致，shimmer 扫光 */
.dashboard-state { display: grid; gap: var(--kg-space-4); }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

.skeleton-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border-radius: var(--kg-radius-lg);
}

.skeleton-cell { height: 88px; }

.skeleton-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.9fr) minmax(290px, 1fr);
  gap: var(--kg-space-4);
}

.skeleton-panel {
  height: 420px;
  border-radius: var(--kg-radius-lg);
}

@media (max-width: 1080px) {
  .skeleton-grid { grid-template-columns: 1fr; }
  .skeleton-panel.is-small { display: none; }
}

.refresh-state {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 2;
  height: 38px;
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.refresh-state :deep(.el-button) { gap: 7px; }

.dashboard-tabs { margin-top: 0; }
.dashboard-tabs :deep(.el-tabs__nav-wrap) { padding-right: 250px; }
.dashboard-tabs :deep(.el-tabs__header) { margin-bottom: var(--kg-space-4); }
.dashboard-tabs :deep(.el-tabs__content) { overflow: visible; }

.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 0;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-sm);
}

.summary-item {
  min-width: 0;
  display: grid;
  gap: 3px;
  padding: var(--kg-space-5) var(--kg-space-6);
  border-right: 1px solid var(--kg-border-subtle);
}
.summary-item:last-child { border-right: 0; }
/* 四项错峰入场（kg-enter 读取 --kg-enter-delay） */
.summary-item:nth-child(2) { --kg-enter-delay: 70ms; }
.summary-item:nth-child(3) { --kg-enter-delay: 140ms; }
.summary-item:nth-child(4) { --kg-enter-delay: 210ms; }
.summary-item strong { overflow: hidden; color: var(--kg-text-primary); font: 650 22px/1.3 var(--kg-font-mono); font-variant-numeric: tabular-nums; text-overflow: ellipsis; white-space: nowrap; }
.summary-item strong.accent { color: var(--kg-accent); }
.summary-item strong.info { color: var(--kg-info); }
.summary-item strong.success { color: var(--kg-success); }
.summary-item strong.warning { color: var(--kg-warning); }
.summary-item strong.danger { color: var(--kg-danger); }
.summary-item small { color: var(--kg-text-tertiary); font-size: 11px; }

.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.9fr) minmax(290px, 1fr);
  gap: var(--kg-space-4);
  margin-top: var(--kg-space-4);
}

.overview-panel {
  min-width: 0;
  padding: var(--kg-space-6);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-sm);
}

.section-head {
  display: flex;
  align-items: flex-end;
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
.collection-state.is-ok .state-dot { animation: kg-dot-breathe 2.4s infinite; }
.collection-state.is-warning { color: var(--kg-warning); }

.state-dot,
.activity-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.platform-identity {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  margin-top: var(--kg-space-4);
  padding: 12px 14px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: #f8faff;
}

.platform-identity.is-success { border-color: rgb(32 154 98 / 22%); background: rgb(32 154 98 / 5%); }
.platform-identity.is-warning { border-color: rgb(183 121 31 / 22%); background: rgb(183 121 31 / 5%); }
.platform-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 10px;
  color: var(--kg-accent);
  background: rgb(23 92 255 / 9%);
}
.platform-copy { min-width: 0; display: grid; gap: 2px; }
.platform-copy > span { color: var(--kg-text-tertiary); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }
.platform-copy strong { overflow: hidden; color: var(--kg-text-primary); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.platform-copy small { overflow: hidden; color: var(--kg-text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.platform-match {
  padding: 4px 8px;
  border-radius: 999px;
  color: var(--kg-text-secondary);
  background: rgb(123 135 157 / 10%);
  font-size: 10px;
  white-space: nowrap;
}
.platform-identity.is-success .platform-match { color: var(--kg-success); background: rgb(32 154 98 / 10%); }
.platform-identity.is-warning .platform-match { color: var(--kg-warning); background: rgb(183 121 31 / 10%); }

.health-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--kg-space-4);
  margin-top: var(--kg-space-6);
}

.health-metric { min-width: 0; display: grid; justify-items: center; gap: 10px; }
.metric-ring {
  width: min(112px, 100%);
  aspect-ratio: 1;
  padding: 7px;
  border-radius: 50%;
  background: conic-gradient(var(--kg-ring-c, #175cff) var(--kg-ring-p, 0%), #edf1f8 var(--kg-ring-p, 0%) 100%);
  box-shadow: inset 0 0 0 1px rgb(23 92 255 / 5%);
  animation: kg-ring-fill 700ms var(--kg-ease-emphasized);
  transition: --kg-ring-p 700ms var(--kg-ease-emphasized);
}
.metric-ring-core {
  width: 100%;
  height: 100%;
  display: grid;
  align-content: center;
  justify-items: center;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 1px #edf1f8;
}
.metric-ring-core strong { color: var(--kg-text-primary); font: 650 18px/1.2 var(--kg-font-mono); }
.metric-ring-core strong.warning { color: var(--kg-warning); }
.metric-ring-core strong.danger { color: var(--kg-danger); }
.metric-ring-core strong.success { color: var(--kg-success); }
.metric-ring-core strong.disabled { color: var(--kg-text-disabled); }
.metric-ring-core span { margin-top: 4px; color: var(--kg-text-tertiary); font-size: 11px; }
.health-metric .metric-note { width: 100%; text-align: center; }

.capability-summary {
  display: grid;
  gap: 10px;
  margin-top: var(--kg-space-5);
  padding-top: var(--kg-space-4);
  border-top: 1px solid var(--kg-border-subtle);
}

.capability-head {
  display: flex;
  align-items: baseline;
  gap: var(--kg-space-2);
}

.capability-head strong {
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.capability-head span {
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.capability-row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: start;
  gap: var(--kg-space-3);
}

.capability-label {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 25px;
  color: var(--kg-text-secondary);
  font-size: 11px;
}

.capability-label .kg-icon { color: var(--kg-accent); }

.capability-label b {
  min-width: 18px;
  padding: 1px 5px;
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-surface-3);
  color: var(--kg-text-tertiary);
  font: 600 10px/1.5 var(--kg-font-mono);
  text-align: center;
}

.capability-items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.capability-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 25px;
  padding: 3px 8px;
  border: 1px solid #dce6f6;
  border-radius: var(--kg-radius-pill);
  background: #f7faff;
  color: var(--kg-text-secondary);
  font-size: 11px;
  line-height: 1.25;
}

.capability-chip small {
  color: var(--kg-accent);
  font-size: 9px;
}

.capability-chip.unavailable {
  border-color: #eadfca;
  background: #fffaf0;
  color: var(--kg-warning);
}

.capability-empty {
  display: inline-flex;
  align-items: center;
  min-height: 25px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

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

/* 视觉隐藏：环内数值与下方备注已可见，此处仅保留给读屏 */
.metric-accessible {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}

.metric-note {
  display: block;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
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
.activity-row dd.success { color: var(--kg-success); }
.activity-row dd.info { color: var(--kg-info); }

.panel-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--kg-space-2);
  min-height: 190px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.details-section { margin-top: 0; }

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
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.raw-output.is-memory-table {
  overflow-wrap: normal;
  white-space: pre;
}

.details-empty { min-height: 120px; }

@media (max-width: 1080px) {
  .overview-grid { grid-template-columns: 1fr; }
  .activity-list { grid-template-columns: 1fr 1fr; column-gap: var(--kg-space-5); }
  .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-item:nth-child(2) { border-right: 0; }
  .summary-item:nth-child(-n + 2) { border-bottom: 1px solid var(--kg-border-subtle); }
}

@media (max-width: 720px) {
  .refresh-state .kg-meta { display: none; }
  .dashboard-tabs :deep(.el-tabs__nav-wrap) { padding-right: 82px; }
  .health-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-ring { width: min(96px, 100%); }
  .capability-row { grid-template-columns: 1fr; gap: 5px; }
}
</style>
