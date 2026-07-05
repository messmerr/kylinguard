<template>
  <div class="dash-page">
    <div class="dash-inner">

      <!-- 安全管道漏斗 -->
      <h3>安全管道对抗态势</h3>
      <div class="funnel-wrap" v-if="stats">
        <div v-for="(stage, i) in pipeline" :key="stage.key" class="stage-col">
          <div class="stage-card" :class="stage.accent">
            <div class="stage-icon">{{ stage.icon }}</div>
            <div class="stage-count">{{ stage.count }}</div>
            <div class="stage-label">{{ stage.label }}</div>
          </div>
          <!-- 拦截/拒绝气泡 -->
          <div v-if="stage.blocked" class="blocked-bubble">
            <span class="blocked-arrow">↓</span>
            <span class="blocked-num">{{ stage.blocked }}</span>
            <span class="blocked-tag">{{ stage.blockedLabel }}</span>
          </div>
          <!-- 批准气泡（确认阶段额外显示） -->
          <div v-if="stage.approved" class="approved-bubble">
            <span class="approved-num">✓ {{ stage.approved }}</span>
            <span class="approved-tag">已批准</span>
          </div>
          <!-- 阶段间箭头 -->
          <div v-if="i < pipeline.length - 1" class="stage-arrow">›</div>
        </div>

        <!-- 拦截率总览 -->
        <div class="intercept-bar">
          <div class="intercept-label">安全拦截率</div>
          <div class="intercept-track">
            <div class="intercept-fill" :style="{ width: interceptPct + '%' }"></div>
          </div>
          <div class="intercept-pct">{{ interceptPct }}%</div>
          <div class="intercept-note">
            （校验拦截 {{ stats.denied }} + 人工拒绝 {{ stats.confirm_rejected }}）/ 规划总步数 {{ planCount }}
          </div>
        </div>
      </div>
      <div v-else class="empty">统计加载中…</div>

      <!-- 聚合数字 -->
      <h3 class="sec-title">运营总量</h3>
      <div class="stat-row" v-if="stats">
        <div class="big-stat"><span class="num">{{ stats.sessions }}</span>累计会话</div>
        <div class="big-stat"><span class="num">{{ stats.total_events }}</span>审计事件</div>
        <div class="big-stat"><span class="num blue">{{ stats.by_type?.execution || 0 }}</span>已执行操作</div>
        <div class="big-stat"><span class="num red">{{ stats.denied }}</span>校验拦截</div>
        <div class="big-stat"><span class="num green">{{ stats.confirm_approved }}</span>人工批准</div>
        <div class="big-stat"><span class="num yellow">{{ stats.confirm_rejected }}</span>人工拒绝</div>
      </div>

      <!-- 系统实时状态 -->
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

const planCount = computed(() => stats.value?.by_type?.plan ?? 0)

const pipeline = computed(() => {
  if (!stats.value) return []
  const s = stats.value
  const bt = s.by_type ?? {}
  return [
    {
      key: 'plan', icon: '⊙', label: '规划', accent: 'accent-blue',
      count: bt.plan ?? 0,
      blocked: null, approved: null,
    },
    {
      key: 'verify', icon: '⊛', label: '校验', accent: 'accent-purple',
      count: bt.verification ?? 0,
      blocked: s.denied, blockedLabel: '拦截',
      approved: null,
    },
    {
      key: 'confirm', icon: '⊡', label: '人工确认', accent: 'accent-orange',
      count: bt.confirm_request ?? 0,
      blocked: s.confirm_rejected, blockedLabel: '拒绝',
      approved: s.confirm_approved,
    },
    {
      key: 'exec', icon: '⊳', label: '执行', accent: 'accent-green',
      count: bt.execution ?? 0,
      blocked: null, approved: null,
    },
  ]
})

const interceptPct = computed(() => {
  const n = planCount.value
  if (!n || !stats.value) return 0
  return Math.min(100, Math.round(
    ((stats.value.denied + stats.value.confirm_rejected) / n) * 100
  ))
})

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

onMounted(() => { poll(); timer = setInterval(poll, 30000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.dash-page { flex: 1; overflow-y: auto; }
.dash-inner { max-width: 1100px; margin: 0 auto; padding: 20px 24px 40px; }
h3 { color: #e6edf3; font-size: 15px; margin: 6px 0 12px; font-weight: 600; }
.sec-title { margin-top: 28px; }
.age { font-weight: 400; font-size: 12px; color: #8b949e; }
.empty { color: #484f58; font-size: 12px; padding: 10px 0; }

/* ---- 漏斗 ---- */
.funnel-wrap {
  background: #0d1117; border: 1px solid #21262d; border-radius: 12px;
  padding: 24px 20px 18px; display: flex; flex-direction: column; gap: 16px;
}

.stage-col {
  display: flex; align-items: center; gap: 0;
  position: relative;
}

/* 横向排列四个阶段 */
.funnel-wrap > .stage-col {
  /* handled by inner flex below */
}

/* 把四个 stage-col 横排 */
.funnel-wrap { flex-direction: row; flex-wrap: wrap; align-items: flex-start; }
.stage-col { flex-direction: column; align-items: center; flex: 1; min-width: 0; }

.stage-card {
  width: 120px; padding: 16px 12px 12px; border-radius: 10px;
  border: 1px solid #21262d; background: #161b22;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  position: relative;
}
.stage-icon { font-size: 18px; color: #8b949e; }
.stage-count { font-size: 28px; font-weight: 700; color: #e6edf3; line-height: 1; }
.stage-label { font-size: 12px; color: #8b949e; }

.accent-blue  { border-color: #1f6feb; }
.accent-blue  .stage-icon { color: #58a6ff; }
.accent-blue  .stage-count { color: #58a6ff; }
.accent-purple { border-color: #6e40c9; }
.accent-purple .stage-icon { color: #a371f7; }
.accent-purple .stage-count { color: #a371f7; }
.accent-orange { border-color: #9e6a03; }
.accent-orange .stage-icon { color: #d29922; }
.accent-orange .stage-count { color: #d29922; }
.accent-green  { border-color: #238636; }
.accent-green  .stage-icon { color: #3fb950; }
.accent-green  .stage-count { color: #3fb950; }

.blocked-bubble {
  display: flex; flex-direction: column; align-items: center;
  margin-top: 8px; gap: 2px;
}
.blocked-arrow { color: #f85149; font-size: 14px; line-height: 1; }
.blocked-num { font-size: 18px; font-weight: 700; color: #f85149; }
.blocked-tag { font-size: 11px; color: #8b949e; }

.approved-bubble {
  display: flex; flex-direction: column; align-items: center;
  margin-top: 4px; gap: 2px;
}
.approved-num { font-size: 13px; font-weight: 600; color: #3fb950; }
.approved-tag { font-size: 11px; color: #8b949e; }

.stage-arrow {
  font-size: 24px; color: #30363d; position: absolute;
  right: -14px; top: 22px; z-index: 1; pointer-events: none;
}

/* 拦截率条 */
.intercept-bar {
  width: 100%; display: flex; align-items: center; gap: 10px;
  padding-top: 16px; border-top: 1px solid #21262d; flex-basis: 100%;
  flex-shrink: 0;
}
.intercept-label { font-size: 12px; color: #8b949e; white-space: nowrap; }
.intercept-track {
  flex: 1; height: 6px; background: #21262d; border-radius: 3px; overflow: hidden;
}
.intercept-fill { height: 100%; background: #f85149; border-radius: 3px; transition: width 0.4s; }
.intercept-pct { font-size: 14px; font-weight: 700; color: #f85149; white-space: nowrap; }
.intercept-note { font-size: 11px; color: #484f58; white-space: nowrap; }

/* ---- 数字卡 ---- */
.stat-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.big-stat { background: #161b22; border: 1px solid #21262d;
  border-radius: 12px; padding: 14px 16px; font-size: 12px; color: #8b949e;
  display: flex; flex-direction: column; gap: 4px; }
.num { font-size: 26px; font-weight: 700; color: #e6edf3; }
.num.green { color: #3fb950; }
.num.yellow { color: #d29922; }
.num.red { color: #f85149; }
.num.blue { color: #58a6ff; }

/* ---- 快照 ---- */
.snap-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.snap-card { background: #161b22; border: 1px solid #21262d;
  border-radius: 12px; padding: 12px 14px; min-width: 0; }
.snap-head { display: flex; justify-content: space-between; margin-bottom: 6px; }
.snap-name { font-size: 13px; color: #e6edf3; font-weight: 600; }
.snap-brief { font-size: 12px; color: #f85149; }
.snap-raw { font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  color: #8b949e; white-space: pre-wrap; word-break: break-all;
  max-height: 180px; overflow-y: auto; margin: 0; }
</style>
