<template>
  <div class="kg-page audit-page">
    <div class="kg-page-inner audit-inner">
      <header class="page-head">
        <div>
          <p class="page-description">按任务查看操作、确认与执行记录。</p>
        </div>
        <el-button :disabled="!selectedId || !events.length" @click="exportReport">
          <KgIcon name="download" :size="15" />
          导出 JSON
        </el-button>
      </header>

      <div class="audit-toolbar">
        <label class="session-picker">
          <span>任务</span>
          <el-select
            :model-value="selectedId"
            filterable
            placeholder="选择一项任务"
            @change="select"
          >
            <el-option
              v-for="session in sessions"
              :key="session.id"
              :label="session.title"
              :value="session.id"
            />
          </el-select>
        </label>

        <div v-if="selectedId" class="chain-summary" aria-live="polite">
          <span class="chain-status" :class="chainStatusClass">
            <KgIcon :name="chainStatusIcon" :size="14" />
            {{ chainStatusText }}
          </span>
          <span class="event-count">{{ events.length }} 条事件</span>
        </div>
      </div>

      <div v-if="loading" class="kg-empty audit-empty">
        <span class="kg-spinner" aria-hidden="true"></span>
        <strong>正在读取审计记录</strong>
      </div>

      <div v-else-if="loadError" class="kg-empty audit-empty is-error">
        <KgIcon name="warning" :size="22" />
        <strong>无法读取审计记录</strong>
        <span>{{ loadError }}</span>
      </div>

      <section v-else-if="selectedId && events.length" class="timeline" aria-label="审计事件">
        <article
          v-for="ev in events"
          :key="ev.seq"
          class="event"
          :class="eventTone(ev)"
        >
          <span class="event-marker" aria-hidden="true">
            <KgIcon :name="eventIcon(ev.event_type)" :size="13" />
          </span>

          <button
            class="event-head"
            type="button"
            :aria-expanded="expanded.has(ev.seq)"
            @click="toggle(ev.seq)"
          >
            <span class="event-seq">#{{ ev.seq }}</span>
            <span class="event-type">{{ typeLabel(ev.event_type) }}</span>
            <span class="event-brief">{{ brief(ev) }}</span>
            <span class="event-ts">
              <span class="event-date">{{ tsParts(ev.ts).date }}</span>
              {{ tsParts(ev.ts).time }}
            </span>
            <code class="event-hash" :title="ev.hash">{{ shortHash(ev.hash) }}</code>
            <KgIcon
              name="chevron"
              :size="14"
              class="event-chevron"
              :class="{ open: expanded.has(ev.seq) }"
            />
          </button>

          <div v-if="expanded.has(ev.seq)" class="event-detail">
            <dl v-if="detailRows(ev).length" class="detail-list">
              <div v-for="row in detailRows(ev)" :key="row.label" class="detail-row">
                <dt>{{ row.label }}</dt>
                <dd :class="{ 'kg-mono': row.mono }">{{ row.value }}</dd>
              </div>
            </dl>

            <div class="hash-chain">
              <div>
                <span>前一事件</span>
                <code>{{ ev.prev_hash }}</code>
              </div>
              <KgIcon name="chevron" :size="13" />
              <div>
                <span>当前事件</span>
                <code>{{ ev.hash }}</code>
              </div>
            </div>

            <button class="raw-toggle" type="button" @click.stop="toggleRaw(ev.seq)">
              {{ rawExpanded.has(ev.seq) ? '收起原始数据' : '查看原始数据' }}
              <KgIcon
                name="chevron"
                :size="13"
                :class="{ open: rawExpanded.has(ev.seq) }"
              />
            </button>
            <pre v-if="rawExpanded.has(ev.seq)" class="event-payload">{{
              JSON.stringify(ev.payload, null, 2)
            }}</pre>
          </div>
        </article>
      </section>

      <div v-else-if="selectedId" class="kg-empty audit-empty">
        <KgIcon name="audit" :size="24" />
        <strong>这项任务还没有审计事件</strong>
      </div>

      <div v-else class="kg-empty audit-empty">
        <KgIcon name="audit" :size="24" />
        <strong>{{ sessions.length ? '选择一项任务查看审计记录' : '还没有可审计的任务' }}</strong>
        <span v-if="sessions.length">每次检查、确认和执行都会记录在这里。</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useAuth.js'
import { refreshSessions, sessions } from '../composables/useChat.js'

const selectedId = ref('')
const events = ref([])
const chainOk = ref(null)
const loading = ref(false)
const loadError = ref('')
const expanded = reactive(new Set())
const rawExpanded = reactive(new Set())
let selectRequest = 0

const TYPE_LABELS = {
  user_query: '管理员指令',
  intent_filter: '意图检查',
  intent_signal: '高风险意图信号',
  snapshot: '系统状态',
  plan: '执行计划',
  verification: '安全检查',
  confirm_request: '请求确认',
  confirm_result: '确认结果',
  permission_changed: '权限模式变更',
  permission_request: '权限请求',
  permission_result: '授权结果',
  permission_resolved: '授权已处理',
  permission_grants_revoked: '授权已收回',
  permission_reauthentication_failed: '身份复验失败',
  permission_request_stale: '权限请求已失效',
  step_rewrite: '命令已改写',
  capability_error: '能力调用受阻',
  execution_authorized: '执行前授权',
  execution_authorization_failed: '执行前权限失效',
  task_error: '任务错误',
  execution: '执行结果',
  final_answer: '最终回复',
}

const ACTION_LABELS = {
  auto: '自动执行',
  confirm: '请求确认',
  double_confirm: '二次确认',
  deny: '已阻止',
}

const RISK_LABELS = { low: '低风险', medium: '中等风险', high: '高风险' }
const RULE_LABELS = { allow: '通过', review: '需复核', deny: '未通过' }

const chainStatusText = computed(() => {
  if (chainOk.value === true) return '链路校验通过'
  if (chainOk.value === false) return '链路校验失败'
  return '正在校验'
})

const chainStatusClass = computed(() => ({
  'is-ok': chainOk.value === true,
  'is-bad': chainOk.value === false,
  'is-pending': chainOk.value == null,
}))

const chainStatusIcon = computed(() => {
  if (chainOk.value === true) return 'check'
  if (chainOk.value === false) return 'warning'
  return 'refresh'
})

const typeLabel = (type) => TYPE_LABELS[type] || type
const actionLabel = (action) => ACTION_LABELS[action] || action || '—'
const riskLabel = (risk) => RISK_LABELS[risk] || risk || '—'

function eventIcon(type) {
  return {
    user_query: 'task', intent_filter: 'shield', intent_signal: 'warning',
    snapshot: 'server', plan: 'task', verification: 'shield',
    confirm_request: 'warning', confirm_result: 'check', execution: 'terminal',
    permission_changed: 'shield', permission_request: 'lock', permission_result: 'check',
    permission_resolved: 'check', permission_grants_revoked: 'lock',
    permission_reauthentication_failed: 'warning', permission_request_stale: 'warning',
    step_rewrite: 'terminal', capability_error: 'warning',
    execution_authorized: 'shield', execution_authorization_failed: 'warning',
    task_error: 'warning',
    final_answer: 'info',
  }[type] || 'audit'
}

function eventTone(ev) {
  const action = ev.payload?.decision?.action
  if (ev.event_type === 'intent_filter'
      || ev.event_type === 'permission_reauthentication_failed'
      || ev.event_type === 'execution_authorization_failed'
      || ev.event_type === 'task_error'
      || (ev.event_type === 'final_answer' && ev.payload?.aborted)
      || action === 'deny'
      || (ev.event_type === 'confirm_result' && !ev.payload?.approved)) {
    return 'is-danger'
  }
  if (ev.event_type === 'confirm_request' || ev.event_type === 'permission_request'
      || ev.event_type === 'intent_signal'
      || action === 'confirm' || action === 'double_confirm') {
    return 'is-warning'
  }
  if (ev.event_type === 'execution') return 'is-info'
  if (ev.event_type === 'final_answer' || ev.event_type === 'confirm_result'
      || ev.event_type === 'permission_result'
      || ev.event_type === 'permission_resolved'
      || ev.event_type === 'execution_authorized') return 'is-success'
  return ''
}

function brief(ev) {
  const p = ev.payload || {}
  switch (ev.event_type) {
    case 'user_query': return p.query || '—'
    case 'intent_filter': return compactText(p.decision?.reason || '请求未通过意图检查', 72)
    case 'intent_signal': return compactText(p.decision?.reason || '进入高风险校验', 72)
    case 'snapshot': return `已读取 ${Object.keys(p.snapshot || {}).length} 项指标`
    case 'plan': return p.steps?.length ? `${p.steps.length} 个步骤` : '直接给出结论'
    case 'verification': {
      const tool = p.step?.tool || '未知工具'
      return `${tool} · ${actionLabel(p.decision?.action)} · ${riskLabel(p.decision?.risk)}`
    }
    case 'confirm_request': return `${p.step?.purpose || p.step?.tool || '操作'} · ${riskLabel(p.decision?.risk)}`
    case 'confirm_result': return `${p.approved ? '已批准' : '已拒绝'} · 操作人 ${p.operator || '—'}`
    case 'permission_changed': return `${p.from_mode || '新任务'} → ${p.to_mode || '—'} · 操作人 ${p.operator || '—'}`
    case 'permission_request': return `${p.step?.purpose || p.capability || '操作'} · ${riskLabel(p.decision?.risk)}`
    case 'permission_result': return `${p.approved ? '已授权' : '已拒绝'} · ${p.decision || '—'} · ${p.operator || '—'}`
    case 'permission_resolved': return `${p.decision || '—'} · ${p.capability || '—'} · ${p.operator || '—'}`
    case 'permission_grants_revoked': return `收回 ${p.revoked_grants ?? 0} 条授权 · ${p.operator || '—'}`
    case 'permission_reauthentication_failed': return `管理员身份复验未通过 · ${p.operator || '—'}`
    case 'permission_request_stale': return `请求版本 ${p.request_version ?? '—'}，当前版本 ${p.current_version ?? '—'}`
    case 'step_rewrite': return compactText(p.reason || p.outcome || '已改写为安全调用', 72)
    case 'capability_error': return `${p.capability || '未知能力'} · ${p.code || '调用受阻'}`
    case 'execution_authorized': return `${p.mode || '—'} · ${p.grant_id ? '使用动作授权' : '由当前模式授权'}`
    case 'execution_authorization_failed': return compactText(p.message || p.reason || p.code || '执行前权限失效', 72)
    case 'task_error': return compactText(p.error?.message || '任务未能完成', 72)
    case 'execution': return `${p.step?.tool || '执行操作'} · ${durationText(p.duration_ms)}`
    case 'final_answer': return compactText(p.answer || '', 72) || '已生成回复'
    default: return '—'
  }
}

function detailRows(ev) {
  const p = ev.payload || {}
  switch (ev.event_type) {
    case 'user_query':
      return [{ label: '指令', value: p.query || '—' }]
    case 'intent_filter':
      return [
        { label: '结果', value: RULE_LABELS[p.decision?.decision] || p.decision?.decision || '未通过' },
        { label: '原因', value: p.decision?.reason || '—' },
        { label: '命中规则', value: p.decision?.matched_rule || '—', mono: true },
      ]
    case 'intent_signal':
      return [
        { label: '信号', value: p.decision?.reason || '—' },
        { label: '处理', value: '继续进入参数、Reviewer 与权限校验' },
      ]
    case 'snapshot':
      return [{ label: '采集项', value: Object.keys(p.snapshot || {}).join('、') || '—' }]
    case 'plan':
      return [
        ...(p.thought ? [{ label: '说明', value: compactText(p.thought, 240) }] : []),
        { label: '步骤', value: planStepsText(p.steps) },
      ]
    case 'verification':
      return [
        { label: '工具', value: p.step?.tool || '—', mono: true },
        { label: '参数', value: argsText(p.step), mono: true },
        { label: '规则检查', value: `${RULE_LABELS[p.rule?.decision] || p.rule?.decision || '—'} · ${p.rule?.reason || '无说明'}` },
        { label: '模型复核', value: `${p.review?.safe && p.review?.matches_intent ? '通过' : '未通过'} · ${p.review?.reason || '无说明'}` },
        { label: '处理方式', value: `${actionLabel(p.decision?.action)} · ${riskLabel(p.decision?.risk)} · ${p.decision?.reason || '无说明'}` },
      ]
    case 'confirm_request':
      return [
        { label: '工具', value: p.step?.tool || '—', mono: true },
        { label: '参数', value: argsText(p.step), mono: true },
        { label: '目的', value: p.step?.purpose || '—' },
        { label: '原因', value: p.decision?.reason || '—' },
      ]
    case 'confirm_result':
      return [
        { label: '结果', value: p.approved ? '批准执行' : '拒绝执行' },
        { label: '操作人', value: p.operator || '—' },
      ]
    case 'permission_changed':
      return [
        { label: '模式', value: `${p.from_mode || '新任务'} → ${p.to_mode || '—'}` },
        { label: '操作人', value: p.operator || '—' },
        { label: '可信目录', value: (p.trusted_roots || []).join('、') || '无', mono: true },
        { label: '权限版本', value: String(p.version ?? '—'), mono: true },
      ]
    case 'permission_request':
      return [
        { label: '工具', value: p.step?.tool || '—', mono: true },
        { label: '能力', value: p.capability || p.action?.capability || '—', mono: true },
        { label: '范围', value: p.resource || p.action?.resource || '—', mono: true },
        { label: '原因', value: p.decision?.reason || '—' },
      ]
    case 'permission_result':
    case 'permission_resolved':
      return [
        { label: '结果', value: p.decision || (p.approved ? '已授权' : '已拒绝') },
        { label: '操作人', value: p.operator || '—' },
        { label: '能力', value: p.capability || '—', mono: true },
        { label: '范围', value: p.resource || p.trusted_path || '—', mono: true },
        { label: '授权编号', value: p.grant_id || '无', mono: true },
      ]
    case 'permission_grants_revoked':
      return [
        { label: '收回数量', value: String(p.revoked_grants ?? 0) },
        { label: '操作人', value: p.operator || '—' },
        { label: '范围', value: p.scope || '—' },
      ]
    case 'permission_reauthentication_failed':
    case 'permission_request_stale':
      return [
        { label: '操作人', value: p.operator || '—' },
        { label: '请求编号', value: p.request_id || '—', mono: true },
        { label: '能力', value: p.capability || '—', mono: true },
      ]
    case 'step_rewrite':
      return [
        { label: '结果', value: p.outcome || '—' },
        { label: '原因', value: p.reason || '—' },
        { label: '建议工具', value: (p.suggested_tools || []).join('、') || '—', mono: true },
      ]
    case 'capability_error':
      return [
        { label: '能力', value: p.capability || '—', mono: true },
        { label: '范围', value: p.resource || '—', mono: true },
        { label: '代码', value: p.code || '—', mono: true },
      ]
    case 'execution_authorized':
    case 'execution_authorization_failed':
      return [
        { label: '权限模式', value: p.mode || '—' },
        { label: '权限版本', value: String(p.context_version ?? p.current_context_version ?? '—'), mono: true },
        { label: '授权编号', value: p.grant_id || '无', mono: true },
        ...(p.message ? [{ label: '原因', value: p.message }] : []),
      ]
    case 'task_error':
      return [
        { label: '阶段', value: p.stage || '—' },
        { label: '错误', value: p.error?.message || '—' },
        { label: '诊断编号', value: p.error?.incident_id || '—', mono: true },
      ]
    case 'execution':
      return [
        { label: '工具', value: p.step?.tool || '—', mono: true },
        { label: '参数', value: argsText(p.step), mono: true },
        { label: '耗时', value: durationText(p.duration_ms), mono: true },
        ...(p.output != null ? [{ label: '输出', value: compactText(String(p.output), 500), mono: true }] : []),
      ]
    case 'final_answer':
      return [{ label: '回复', value: p.answer || '—' }]
    default:
      return []
  }
}

function planStepsText(steps = []) {
  if (!steps.length) return '无执行步骤'
  return steps.map((step, index) => `${index + 1}. ${step.purpose || step.tool}`).join('；')
}

function argsText(step) {
  const args = step?.arguments ?? step?.args ?? {}
  return Object.keys(args).length ? JSON.stringify(args) : '—'
}

function compactText(value, max) {
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function durationText(ms) {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function shortHash(hash = '') {
  return hash ? `${hash.slice(0, 10)}…` : '—'
}

function tsParts(ts) {
  const date = new Date(ts)
  const dateText = date.toLocaleDateString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
  })
  const timeText = date.toLocaleTimeString('zh-CN', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  return { date: dateText, time: timeText }
}

function toggle(seq) {
  expanded.has(seq) ? expanded.delete(seq) : expanded.add(seq)
}

function toggleRaw(seq) {
  rawExpanded.has(seq) ? rawExpanded.delete(seq) : rawExpanded.add(seq)
}

async function select(id) {
  const requestId = ++selectRequest
  selectedId.value = id || ''
  events.value = []
  chainOk.value = null
  loadError.value = ''
  expanded.clear()
  rawExpanded.clear()
  if (!id) {
    loading.value = false
    return
  }

  loading.value = true
  try {
    const [eventResponse, verifyResponse] = await Promise.all([
      apiFetch(`/api/sessions/${id}/events`),
      apiFetch(`/api/sessions/${id}/verify`),
    ])
    if (!eventResponse.ok || !verifyResponse.ok) throw new Error('服务器返回了错误状态')
    const eventBody = await eventResponse.json()
    const verifyBody = await verifyResponse.json()
    if (requestId !== selectRequest || selectedId.value !== id) return
    events.value = eventBody.events || []
    chainOk.value = verifyBody.ok
  } catch (error) {
    if (requestId === selectRequest && selectedId.value === id) {
      loadError.value = error.message || '请稍后重试'
    }
  } finally {
    if (requestId === selectRequest) loading.value = false
  }
}

function exportReport() {
  const report = {
    session_id: selectedId.value,
    exported_at: new Date().toISOString(),
    chain_verified: chainOk.value,
    events: events.value,
  }
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
  const anchor = document.createElement('a')
  anchor.href = URL.createObjectURL(blob)
  anchor.download = `kylinguard-audit-${selectedId.value.slice(0, 8)}.json`
  anchor.click()
  URL.revokeObjectURL(anchor.href)
}

onMounted(refreshSessions)
</script>

<style scoped>
.audit-inner {
  width: min(100%, 1100px);
  min-height: 100%;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-6);
}

.page-head :deep(.el-button) {
  gap: 7px;
}

.page-description {
  margin: 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
}

.audit-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--kg-space-5);
  margin-top: var(--kg-space-6);
  padding-bottom: var(--kg-space-4);
  border-bottom: 1px solid var(--kg-border-subtle);
}

.session-picker {
  display: grid;
  gap: 6px;
  width: min(380px, 45%);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.chain-summary {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
  min-height: 32px;
}

.chain-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
}

.chain-status.is-ok { color: var(--kg-success); }
.chain-status.is-bad { color: var(--kg-danger); }
.chain-status.is-pending { color: var(--kg-text-tertiary); }
.chain-status.is-pending :deep(.kg-icon) { animation: kg-spin .8s linear infinite; }
.event-count { color: var(--kg-text-tertiary); font-size: 12px; }

.timeline {
  padding: var(--kg-space-4) 0 var(--kg-space-6);
}

.event {
  position: relative;
  padding-left: 28px;
}

.event:not(:last-child)::before {
  position: absolute;
  top: 27px;
  bottom: -7px;
  left: 8px;
  width: 1px;
  background: var(--kg-border-subtle);
  content: '';
}

.event-marker {
  position: absolute;
  z-index: 1;
  top: 10px;
  left: 0;
  display: grid;
  width: 17px;
  height: 17px;
  place-items: center;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-canvas);
  color: var(--kg-text-tertiary);
}

.event.is-warning .event-marker { border-color: var(--kg-warning-border); color: var(--kg-warning); }
.event.is-danger .event-marker { border-color: var(--kg-danger-border); color: var(--kg-danger); }
.event.is-info .event-marker { border-color: var(--kg-info-border); color: var(--kg-info); }
.event.is-success .event-marker { border-color: var(--kg-success-border); color: var(--kg-success); }

.event-head {
  display: grid;
  grid-template-columns: 34px 112px minmax(120px, 1fr) 174px 108px 14px;
  align-items: center;
  gap: var(--kg-space-2);
  width: 100%;
  min-height: 38px;
  padding: 5px 8px;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--kg-motion-fast) var(--kg-ease-standard);
}

.event-head:hover { background: var(--kg-bg-surface-1); }
.event-seq { color: var(--kg-text-disabled); font-family: var(--kg-font-mono); font-size: 11px; }
.event-type { color: var(--kg-text-primary); font-size: 13px; font-weight: 500; }

.event-brief {
  overflow: hidden;
  color: var(--kg-text-secondary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-ts {
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.event-date { margin-right: 5px; }

.event-hash {
  overflow: hidden;
  color: var(--kg-text-disabled);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-chevron,
.raw-toggle :deep(.kg-icon) {
  color: var(--kg-text-disabled);
  transition: transform var(--kg-motion-base) var(--kg-ease-standard);
}

.event-chevron.open,
.raw-toggle :deep(.kg-icon.open) { transform: rotate(90deg); }

.event-detail {
  margin: 2px 8px var(--kg-space-3);
  padding: var(--kg-space-4);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.detail-list { display: grid; gap: 9px; margin: 0; }

.detail-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: var(--kg-space-3);
}

.detail-row dt {
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.detail-row dd {
  min-width: 0;
  margin: 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.hash-chain {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 14px minmax(0, 1fr);
  align-items: center;
  gap: var(--kg-space-2);
  margin-top: var(--kg-space-4);
  padding-top: var(--kg-space-3);
  border-top: 1px solid var(--kg-border-subtle);
  color: var(--kg-text-disabled);
}

.hash-chain > div { min-width: 0; }
.hash-chain span { display: block; margin-bottom: 3px; font-size: 11px; }

.hash-chain code {
  display: block;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.raw-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: var(--kg-space-3);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  cursor: pointer;
}

.raw-toggle:hover { color: var(--kg-text-primary); }

.event-payload {
  max-height: 320px;
  margin: var(--kg-space-3) 0 0;
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

.audit-empty { min-height: 280px; align-content: center; }
.audit-empty.is-error { color: var(--kg-danger); }

@media (max-width: 1320px) {
  .event-head {
    grid-template-columns: 30px 104px minmax(100px, 1fr) 82px 102px 14px;
  }

  .event-date { display: none; }
}

@media (max-width: 1080px) {
  .session-picker { width: min(340px, 52%); }

  .event-head {
    grid-template-columns: 28px 96px minmax(100px, 1fr) 72px 14px;
  }

  .event-hash { display: none; }
  .event-detail { margin-right: 0; }
}
</style>
