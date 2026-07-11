<template>
  <article class="step-block" :class="[`risk-${riskClass}`, `is-${step.status}`, { open: step.expanded }]">
    <button type="button" class="step-header" :aria-expanded="step.expanded"
            @click="step.expanded = !step.expanded">
      <span class="status-node" :class="step.status">
        <span v-if="isActive" class="node-spinner" aria-hidden="true"></span>
        <KgIcon v-else :name="statusIcon" :size="13" />
      </span>
      <span class="step-identity">
        <strong>{{ step.purpose || shortTool }}</strong>
        <code>{{ shortTool }}</code>
      </span>
      <span class="spacer"></span>
      <span class="risk-badge" :class="riskClass">{{ riskLabel }}</span>
      <span v-if="step.status !== 'denied' && step.status !== 'skipped'"
            class="status-text" :class="step.status">{{ statusText }}</span>
      <span v-if="step.durationMs != null" class="duration">{{ durationText }}</span>
      <KgIcon name="chevron" :size="14" class="chevron"
              :class="{ open: step.expanded }" />
    </button>

    <button v-if="preview && !step.expanded" type="button" class="preview-line"
            @click="step.expanded = true">
      <KgIcon name="terminal" :size="12" />
      <span>{{ preview }}</span>
      <small>查看输出</small>
    </button>

    <div v-if="step.expanded" class="step-body">
      <div v-if="argsText" class="detail-row parameter-row">
        <span class="detail-label">参数</span>
        <code>{{ argsText }}</code>
      </div>

      <section class="checks" aria-label="安全检查">
        <h4>安全检查</h4>
        <template v-if="step.verification">
          <div class="check-row">
            <span class="detail-label">规则检查</span>
            <span class="check-result" :class="ruleClass">{{ ruleLabel }}</span>
            <span class="check-reason">{{ step.verification.rule.reason }}</span>
          </div>
          <div class="check-row">
            <span class="detail-label">模型复核</span>
            <span class="check-result" :class="reviewClass">{{ reviewLabel }}</span>
            <span class="check-reason">{{ step.verification.review.reason }}</span>
          </div>
          <div class="check-row">
            <span class="detail-label">处理方式</span>
            <span class="check-result" :class="decisionClass">{{ decisionLabel }}</span>
            <span class="check-reason">{{ step.verification.decision.reason }}</span>
          </div>
        </template>
        <div v-else class="check-pending">
          <span class="node-spinner" aria-hidden="true"></span>
          <span>正在检查操作风险…</span>
        </div>
      </section>

      <template v-if="step.output != null">
        <div class="output-head">
          <span>原始输出</span>
          <span v-if="step.durationMs != null">{{ durationText }}</span>
        </div>
        <pre class="output-block">{{ step.output }}</pre>
      </template>
    </div>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import KgIcon from './KgIcon.vue'

const props = defineProps({ step: { type: Object, required: true } })
const step = props.step

const shortTool = computed(() => step.tool.split('.').pop())
const argsText = computed(() => {
  const entries = Object.entries(step.args || {})
  return entries.length ? JSON.stringify(step.args) : ''
})

const riskClass = computed(() => {
  if (step.status === 'denied' || step.status === 'skipped') return 'deny'
  return step.verification?.decision.risk || step.risk || 'low'
})

const riskLabel = computed(() => {
  if (step.status === 'skipped') return '已取消'
  return ({
    low: '低风险', medium: '中风险', high: '高风险', deny: '已阻止',
  }[riskClass.value] || '待检查')
})

const isActive = computed(() => ['verifying', 'reviewing', 'running'].includes(step.status))
const statusIcon = computed(() => {
  if (step.status === 'done') return 'check'
  if (step.status === 'denied' || step.status === 'skipped') return 'close'
  if (step.status === 'waiting') return 'warning'
  return 'terminal'
})

const statusText = computed(() => {
  switch (step.status) {
    case 'verifying': return '检查中'
    case 'reviewing': return '复核中'
    case 'waiting': return '等待确认'
    case 'running': return '执行中'
    case 'done': return step.autoAllowed ? '自动执行' : '已执行'
    case 'denied': return '已阻止'
    case 'skipped': return '已取消'
    default: return ''
  }
})

const durationText = computed(() => {
  const ms = step.durationMs
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

const preview = computed(() => {
  if (step.status !== 'done' || !step.output) return ''
  const lines = step.output.split('\n').filter((line) => line.trim())
  const head = lines.slice(0, 2).join('  ')
  return lines.length > 2 ? `${head}  …` : head
})

const ruleLabel = computed(() => {
  const decision = step.verification?.rule?.decision
  if (decision === 'deny') return '未通过'
  if (decision === 'allow') return '通过'
  return '需复核'
})
const ruleClass = computed(() => (
  step.verification?.rule?.decision === 'deny' ? 'danger'
    : step.verification?.rule?.decision === 'allow' ? 'success' : 'info'
))

const reviewLabel = computed(() => (
  step.verification?.review?.safe && step.verification?.review?.matches_intent
    ? '通过' : '未通过'
))
const reviewClass = computed(() => (reviewLabel.value === '通过' ? 'success' : 'danger'))

const decisionLabel = computed(() => ({
  auto: '自动执行', confirm: '需要确认', double_confirm: '再次确认', deny: '已阻止',
}[step.verification?.decision?.action] || '待确定'))
const decisionClass = computed(() => ({
  auto: 'success', confirm: 'warning', double_confirm: 'danger', deny: 'danger',
}[step.verification?.decision?.action] || 'info'))
</script>

<style scoped>
.step-block { position: relative; margin: 6px 0; }
.step-block::before { content: ''; position: absolute; top: 36px; bottom: -9px; left: 20px; width: 1px; background: var(--kg-border-subtle); }

.step-header {
  width: 100%;
  min-height: 40px;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 4px 7px;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  text-align: left;
  cursor: pointer;
  transition: background var(--kg-motion-fast), border-color var(--kg-motion-fast);
}

.step-header:hover,
.step-block.open .step-header { border-color: var(--kg-border-subtle); background: var(--kg-bg-surface-1); }

.status-node {
  position: relative;
  z-index: 1;
  width: 28px;
  height: 28px;
  display: grid;
  flex: none;
  place-items: center;
  border: 1px solid var(--kg-border-default);
  border-radius: 50%;
  background: var(--kg-bg-canvas);
  color: var(--kg-text-tertiary);
}

.status-node.done { border-color: var(--kg-success-border); background: var(--kg-success-soft); color: var(--kg-success); }
.status-node.waiting { border-color: var(--kg-warning-border); background: var(--kg-warning-soft); color: var(--kg-warning); }
.status-node.denied,
.status-node.skipped { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); }
.status-node.verifying,
.status-node.reviewing,
.status-node.running { border-color: var(--kg-info-border); background: var(--kg-info-soft); color: var(--kg-info); }

.node-spinner { width: 12px; height: 12px; flex: none; border: 2px solid var(--kg-border-default); border-top-color: currentColor; border-radius: 50%; animation: kg-spin 800ms linear infinite; }

.step-identity { min-width: 0; display: flex; align-items: baseline; gap: 8px; }
.step-identity strong { overflow: hidden; color: var(--kg-text-primary); font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.step-identity code { color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); }
.spacer { flex: 1; }

.risk-badge { flex: none; padding: 1px 6px; border: 1px solid var(--kg-success-border); border-radius: var(--kg-radius-xs); background: var(--kg-success-soft); color: var(--kg-success); font-size: 12px; }
.risk-badge.medium { border-color: var(--kg-warning-border); background: var(--kg-warning-soft); color: var(--kg-warning); }
.risk-badge.high,
.risk-badge.deny { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); }
.status-text { flex: none; color: var(--kg-text-tertiary); font-size: 12px; }
.status-text.done { color: var(--kg-success); }
.status-text.waiting { color: var(--kg-warning); }
.status-text.denied,
.status-text.skipped { color: var(--kg-danger); }
.status-text.verifying,
.status-text.reviewing,
.status-text.running { color: var(--kg-info); }
.duration { flex: none; color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); }
.chevron { flex: none; color: var(--kg-text-tertiary); transition: transform var(--kg-motion-base); }
.chevron.open { transform: rotate(90deg); }

.preview-line {
  width: calc(100% - 39px);
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  margin: -1px 0 5px 39px;
  padding: 4px 7px;
  overflow: hidden;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  text-align: left;
  cursor: pointer;
}
.preview-line:hover { background: var(--kg-bg-surface-1); color: var(--kg-text-secondary); }
.preview-line > span { min-width: 0; flex: 1; overflow: hidden; font: 12px/1.5 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.preview-line small { flex: none; color: var(--kg-accent); font-size: 12px; }

.step-body {
  margin: 3px 0 14px 39px;
  overflow: hidden;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.detail-row,
.check-row { display: grid; grid-template-columns: 74px 74px minmax(0, 1fr); gap: 10px; align-items: baseline; padding: 9px 12px; }
.parameter-row { grid-template-columns: 74px minmax(0, 1fr); border-bottom: 1px solid var(--kg-border-subtle); }
.parameter-row code { color: var(--kg-text-secondary); font: 12px/1.55 var(--kg-font-mono); word-break: break-all; }
.detail-label { color: var(--kg-text-tertiary); font-size: 12px; }

.checks h4,
.output-head { margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--kg-border-subtle); color: var(--kg-text-secondary); font-size: 12px; font-weight: 600; }
.check-row + .check-row { border-top: 1px solid var(--kg-border-subtle); }
.check-result { font-size: 12px; }
.check-result.success { color: var(--kg-success); }
.check-result.warning { color: var(--kg-warning); }
.check-result.danger { color: var(--kg-danger); }
.check-result.info { color: var(--kg-info); }
.check-reason { color: var(--kg-text-secondary); font-size: 12px; line-height: 1.55; }
.check-pending { display: flex; align-items: center; gap: 8px; padding: 12px; color: var(--kg-text-tertiary); font-size: 12px; }

.output-head { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--kg-border-subtle); }
.output-head span:last-child { color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); font-weight: 400; }
.output-block { max-height: 280px; margin: 0; padding: 11px 12px; overflow: auto; background: var(--kg-bg-code); color: var(--kg-text-secondary); font: 12px/1.55 var(--kg-font-mono); white-space: pre-wrap; word-break: break-all; }

@media (max-width: 1080px) {
  .step-identity code,
  .status-text { display: none; }
}
</style>
