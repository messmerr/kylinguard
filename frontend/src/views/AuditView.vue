<template>
  <div class="audit-layout">
    <aside class="audit-side">
      <div class="side-title">审计会话</div>
      <div class="audit-list">
        <div v-for="s in sessions" :key="s.id" class="audit-item"
             :class="{ active: s.id === selectedId }" @click="select(s.id)">
          <span class="item-title">{{ s.title }}</span>
        </div>
        <div v-if="!sessions.length" class="empty">暂无会话</div>
      </div>
    </aside>

    <main class="audit-main">
      <template v-if="selectedId">
        <div class="audit-head">
          <el-tag v-if="chainOk === true" type="success">✓ 哈希链完整，未检测到篡改</el-tag>
          <el-tag v-else-if="chainOk === false" type="danger">⚠ 校验失败：审计链已被篡改！</el-tag>
          <el-tag v-else type="info">校验中…</el-tag>
          <span class="event-count">{{ events.length }} 条事件</span>
          <el-button size="small" @click="exportReport">导出报告</el-button>
        </div>

        <div class="timeline">
          <div v-for="ev in events" :key="ev.seq" class="event"
               :class="typeClass(ev.event_type)">
            <div class="event-head" @click="toggle(ev.seq)">
              <span class="event-seq">#{{ ev.seq }}</span>
              <span class="event-type">{{ typeLabel(ev.event_type) }}</span>
              <span class="event-brief">{{ brief(ev) }}</span>
              <span class="event-ts">{{ tsText(ev.ts) }}</span>
              <code class="event-hash">{{ ev.hash.slice(0, 12) }}…</code>
            </div>
            <pre v-if="expanded.has(ev.seq)" class="event-payload">{{
              JSON.stringify(ev.payload, null, 2) }}</pre>
          </div>
        </div>
      </template>
      <div v-else class="placeholder">← 选择一个会话查看完整审计链</div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../composables/useAuth.js'
import { refreshSessions, sessions } from '../composables/useChat.js'

const selectedId = ref('')
const events = ref([])
const chainOk = ref(null)
const expanded = reactive(new Set())

const TYPE_LABELS = {
  user_query: '管理员指令', snapshot: '① 感知快照', plan: '② 规划',
  verification: '③ 三道闸校验', confirm_request: '⚠ 请求确认',
  confirm_result: '确认决断', execution: '④ 执行', final_answer: '最终结论',
}

const typeLabel = (t) => TYPE_LABELS[t] || t
const typeClass = (t) => ({
  verification: 'is-verify', confirm_request: 'is-confirm',
  confirm_result: 'is-confirm', execution: 'is-exec',
  final_answer: 'is-final',
}[t] || '')

function brief(ev) {
  const p = ev.payload
  switch (ev.event_type) {
    case 'user_query': return p.query
    case 'plan': return p.steps?.length
      ? `${p.steps.length} 个步骤` : '给出结论'
    case 'verification':
      return `${p.step?.tool} → ${p.decision?.action}（${p.decision?.risk}）`
    case 'confirm_result':
      return `${p.approved ? '批准' : '拒绝'} · 操作人 ${p.operator || '—'}`
    case 'confirm_request': return p.step?.tool
    case 'execution': return `${p.step?.tool} · ${p.duration_ms ?? '—'}ms`
    case 'final_answer': return (p.answer || '').slice(0, 60)
    case 'snapshot': return `${Object.keys(p.snapshot || {}).length} 项指标`
    default: return ''
  }
}

const tsText = (ts) => new Date(ts).toLocaleString('zh-CN', { hour12: false })

function toggle(seq) {
  expanded.has(seq) ? expanded.delete(seq) : expanded.add(seq)
}

async function select(id) {
  selectedId.value = id
  events.value = []
  chainOk.value = null
  expanded.clear()
  const [evR, vfR] = await Promise.all([
    apiFetch(`/api/sessions/${id}/events`),
    apiFetch(`/api/sessions/${id}/verify`),
  ])
  events.value = (await evR.json()).events
  chainOk.value = (await vfR.json()).ok
}

function exportReport() {
  const report = {
    session_id: selectedId.value,
    exported_at: new Date().toISOString(),
    chain_verified: chainOk.value,
    events: events.value,
  }
  const blob = new Blob([JSON.stringify(report, null, 2)],
                        { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `kylinguard-audit-${selectedId.value.slice(0, 8)}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}

onMounted(refreshSessions)
</script>

<style scoped>
.audit-layout { display: flex; flex: 1; min-height: 0; }
.audit-side { width: 230px; flex-shrink: 0; border-right: 1px solid #21262d;
  background: #010409; display: flex; flex-direction: column; }
.side-title { padding: 14px 16px 8px; font-size: 12px; color: #8b949e;
  font-weight: 600; }
.audit-list { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
.audit-item { padding: 8px 10px; border-radius: 8px; cursor: pointer;
  color: #c9d1d9; font-size: 13px; }
.audit-item:hover { background: #161b22; }
.audit-item.active { background: #1c2733; }
.item-title { display: block; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.empty { color: #484f58; font-size: 12px; text-align: center; padding: 20px 0; }

.audit-main { flex: 1; min-width: 0; display: flex; flex-direction: column;
  padding: 14px 20px; overflow: hidden; }
.audit-head { display: flex; align-items: center; gap: 12px;
  margin-bottom: 12px; }
.event-count { color: #8b949e; font-size: 12px; flex: 1; }
.placeholder { color: #484f58; margin: auto; font-size: 14px; }

.timeline { flex: 1; overflow-y: auto; border-left: 2px solid #21262d;
  padding-left: 14px; }
.event { margin-bottom: 4px; position: relative; }
.event::before { content: ''; position: absolute; left: -19px; top: 10px;
  width: 8px; height: 8px; border-radius: 50%; background: #30363d; }
.event.is-verify::before { background: #d29922; }
.event.is-confirm::before { background: #f0883e; }
.event.is-exec::before { background: #58a6ff; }
.event.is-final::before { background: #3fb950; }
.event-head { display: flex; align-items: baseline; gap: 10px;
  padding: 4px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.event-head:hover { background: #161b22; }
.event-seq { color: #484f58; font-size: 11px; width: 30px; flex-shrink: 0; }
.event-type { color: #e6edf3; flex-shrink: 0; }
.event-brief { color: #8b949e; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; flex: 1; }
.event-ts { color: #484f58; font-size: 11px; flex-shrink: 0; }
.event-hash { color: #484f58; font-size: 11px; flex-shrink: 0;
  font-family: ui-monospace, Consolas, monospace; }
.event-payload { margin: 2px 0 8px 40px; padding: 8px 12px;
  background: #161b22; border-radius: 8px; font-size: 11px; color: #c9d1d9;
  font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap;
  word-break: break-all; max-height: 300px; overflow-y: auto; }
</style>
