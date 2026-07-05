<template>
  <div class="dash-page">
    <div class="dash-inner">
      <h3>安全运营总览</h3>
      <div class="stat-row" v-if="stats">
        <div class="big-stat"><span class="num">{{ stats.sessions }}</span>累计会话</div>
        <div class="big-stat"><span class="num">{{ stats.total_events }}</span>审计事件</div>
        <div class="big-stat"><span class="num blue">{{ stats.by_type?.execution || 0 }}</span>已执行操作</div>
        <div class="big-stat"><span class="num red">{{ stats.denied }}</span>安全拦截</div>
        <div class="big-stat"><span class="num green">{{ stats.confirm_approved }}</span>确认批准</div>
        <div class="big-stat"><span class="num yellow">{{ stats.confirm_rejected }}</span>确认拒绝</div>
      </div>

      <h3 class="sec-title">系统实时状态
        <span class="age" v-if="status">（{{ ageText }}采集，30s 自动刷新）</span>
      </h3>
      <div class="snap-grid" v-if="status">
        <div v-for="m in metrics" :key="m.key" class="snap-card">
          <div class="snap-head">
            <span class="snap-name">{{ m.title }}</span>
            <span class="snap-brief">{{ m.brief }}</span>
          </div>
          <pre class="snap-raw">{{ m.raw }}</pre>
        </div>
      </div>
      <div v-else class="empty">状态加载中…</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { apiFetch } from '../composables/useAuth.js'

const stats = ref(null)
const status = ref(null)
let timer = null

const TITLES = {
  uptime_load: '运行时长与负载', memory: '内存', disk: '磁盘',
  top_cpu: 'CPU 占用最高进程', failed_units: '失败服务',
  recent_errors: '近期错误日志',
}

const metrics = computed(() => {
  if (!status.value) return []
  return Object.entries(status.value.snapshot).map(([key, raw]) => ({
    key, raw, title: TITLES[key] || key,
    brief: raw.startsWith('[采集失败]') ? '不可用' : '',
  }))
})

const ageText = computed(() => {
  const age = status.value?.collected_ago_seconds ?? 0
  return age < 3 ? '刚刚' : `${Math.round(age)} 秒前`
})

async function poll() {
  try {
    const [stR, stsR] = await Promise.all([
      apiFetch('/api/status'), apiFetch('/api/stats')])
    if (stR.ok) status.value = await stR.json()
    if (stsR.ok) stats.value = await stsR.json()
  } catch { /* 下轮重试 */ }
}

onMounted(() => {
  poll()
  timer = setInterval(poll, 30000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.dash-page { flex: 1; overflow-y: auto; }
.dash-inner { max-width: 1100px; margin: 0 auto; padding: 20px 24px 40px; }
h3 { color: #e6edf3; font-size: 15px; margin: 6px 0 12px; }
.sec-title { margin-top: 26px; }
.age { font-weight: 400; font-size: 12px; color: #8b949e; }
.stat-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.big-stat { background: #161b22; border: 1px solid #21262d;
  border-radius: 12px; padding: 14px 16px; font-size: 12px; color: #8b949e;
  display: flex; flex-direction: column; gap: 4px; }
.num { font-size: 26px; font-weight: 700; color: #e6edf3; }
.num.green { color: #3fb950; }
.num.yellow { color: #d29922; }
.num.red { color: #f85149; }
.num.blue { color: #58a6ff; }
.snap-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.snap-card { background: #161b22; border: 1px solid #21262d;
  border-radius: 12px; padding: 12px 14px; min-width: 0; }
.snap-head { display: flex; justify-content: space-between;
  margin-bottom: 6px; }
.snap-name { font-size: 13px; color: #e6edf3; font-weight: 600; }
.snap-brief { font-size: 12px; color: #f85149; }
.snap-raw { font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  color: #8b949e; white-space: pre-wrap; word-break: break-all;
  max-height: 180px; overflow-y: auto; margin: 0; }
.empty { color: #484f58; font-size: 12px; }
</style>
