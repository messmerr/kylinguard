<template>
  <aside class="status-panel">
    <div class="panel-title">系统实时状态
      <span class="age" v-if="status">{{ ageText }}</span>
    </div>
    <div v-if="status" class="metric-list">
      <div v-for="m in metrics" :key="m.key" class="metric"
           @click="expanded = expanded === m.key ? '' : m.key">
        <div class="metric-head">
          <span class="metric-name">{{ m.title }}</span>
          <span class="metric-value">{{ m.brief }}</span>
        </div>
        <pre v-if="expanded === m.key" class="metric-detail">{{ m.raw }}</pre>
      </div>
    </div>
    <div v-else class="empty">状态加载中…</div>

    <div class="panel-title stats-title">本会话安全统计</div>
    <div class="stat-grid">
      <div class="stat"><span class="num">{{ stats.steps }}</span>工具调用</div>
      <div class="stat"><span class="num green">{{ stats.auto }}</span>自动放行</div>
      <div class="stat"><span class="num yellow">{{ stats.confirmed }}</span>确认执行</div>
      <div class="stat"><span class="num red">{{ stats.denied }}</span>已拦截</div>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { apiFetch } from '../composables/useAuth.js'
import { stats } from '../composables/useChat.js'

const status = ref(null)
const expanded = ref('')
let timer = null

const TITLES = {
  uptime_load: '负载', memory: '内存', disk: '磁盘',
  top_cpu: 'CPU 进程', failed_units: '失败服务', recent_errors: '错误日志',
}

function brief(key, raw) {
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
      for (const l of lines.slice(1)) {
        const mPct = l.match(/(\d+)%/)
        if (mPct) { max = Math.max(max, +mPct[1]); continue }
        const mWin = l.match(/used=([\d.]+)G.*?total=([\d.]+)G|total=([\d.]+)G used=([\d.]+)G/)
        if (mWin) {
          const used = +(mWin[1] || mWin[4]), total = +(mWin[2] || mWin[3])
          if (total > 0) max = Math.max(max, Math.round(used / total * 100))
        }
      }
      // Windows disk 行格式: "C    total=449.4G used=439.3G free=10.1G"
      if (max === 0) {
        for (const l of lines) {
          const m = l.match(/total=([\d.]+)G\s+used=([\d.]+)G/)
          if (m) max = Math.max(max, Math.round(+m[2] / +m[1] * 100))
        }
      }
      return `最高 ${max}%`
    }
    case 'failed_units':
      return /0 loaded units/.test(raw) || lines.length <= 1 ? '无' : '有失败项'
    case 'top_cpu':
      return `${Math.max(lines.length - 1, 0)} 条`
    case 'recent_errors':
      return `${Math.max(lines.length, 0)} 条`
    default:
      return lines[0]?.slice(0, 16) || '—'
  }
}

const metrics = computed(() => {
  if (!status.value) return []
  return Object.entries(status.value.snapshot).map(([key, raw]) => ({
    key, raw, title: TITLES[key] || key, brief: brief(key, raw),
  }))
})

const ageText = computed(() => {
  const age = status.value?.collected_ago_seconds ?? 0
  return age < 3 ? '刚刚' : `${Math.round(age)}s 前`
})

async function poll() {
  try {
    const r = await apiFetch('/api/status')
    if (r.ok) status.value = await r.json()
  } catch { /* 下轮重试 */ }
}

onMounted(() => {
  poll()
  timer = setInterval(poll, 30000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.status-panel { width: 250px; flex-shrink: 0; border-left: 1px solid #21262d;
  background: #010409; padding: 14px 12px; overflow-y: auto; }
.panel-title { font-size: 12px; color: #8b949e; font-weight: 600;
  margin-bottom: 8px; display: flex; justify-content: space-between; }
.age { font-weight: 400; }
.metric { background: #0d1117; border: 1px solid #21262d; border-radius: 8px;
  padding: 8px 10px; margin-bottom: 6px; cursor: pointer; }
.metric:hover { border-color: #30363d; }
.metric-head { display: flex; justify-content: space-between;
  align-items: center; }
.metric-name { font-size: 12px; color: #c9d1d9; }
.metric-value { font-size: 12px; color: #58a6ff; }
.metric-detail { font-family: ui-monospace, Consolas, monospace;
  font-size: 11px; color: #8b949e; white-space: pre-wrap;
  word-break: break-all; max-height: 200px; overflow-y: auto;
  margin: 6px 0 0; }
.empty { color: #484f58; font-size: 12px; }
.stats-title { margin-top: 18px; }
.stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.stat { background: #0d1117; border: 1px solid #21262d; border-radius: 8px;
  padding: 8px 10px; font-size: 11px; color: #8b949e;
  display: flex; flex-direction: column; gap: 2px; }
.num { font-size: 18px; font-weight: 700; color: #e6edf3; }
.num.green { color: #3fb950; }
.num.yellow { color: #d29922; }
.num.red { color: #f85149; }
</style>
