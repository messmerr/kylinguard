<template>
  <div class="step-block">
    <div class="step-header" @click="step.expanded = !step.expanded">
      <span class="bullet" :class="riskClass">●</span>
      <span class="tool-name">{{ shortTool }}</span>
      <span v-if="argsText" class="args">{{ argsText }}</span>
      <span class="spacer"></span>
      <span class="status-badge" :class="step.status">{{ statusText }}</span>
      <span v-if="step.durationMs != null" class="duration">{{ durationText }}</span>
      <span class="chevron" :class="{ open: step.expanded }">›</span>
    </div>

    <!-- 折叠时：输出首行预览 -->
    <div v-if="preview && !step.expanded" class="preview-line"
         @click="step.expanded = true">
      <span class="tree-indent">│</span>
      <span class="preview-text">{{ preview }}</span>
    </div>

    <!-- 展开时：三道闸 + 完整输出 -->
    <div v-if="step.expanded" class="step-body">
      <template v-if="step.verification">
        <div class="guard-row">
          <span class="tree-indent">├─</span>
          <span class="guard-label">规则引擎</span>
          <span class="guard-text">{{ step.verification.rule.reason }}</span>
        </div>
        <div class="guard-row">
          <span class="tree-indent">├─</span>
          <span class="guard-label">LLM 审查</span>
          <span class="guard-text">{{ step.verification.review.reason }}</span>
        </div>
        <div class="guard-row">
          <span class="tree-indent">└─</span>
          <span class="guard-label">门控结论</span>
          <span class="guard-text">{{ step.verification.decision.reason }}</span>
        </div>
      </template>
      <div v-else class="guard-row">
        <span class="tree-indent">└─</span>
        <span class="guard-text muted">三道闸校验中…</span>
      </div>

      <template v-if="step.output != null">
        <div class="output-header">
          <span class="tree-indent">│</span>
          <span class="guard-label">输出</span>
        </div>
        <pre class="output-block">{{ step.output }}</pre>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ step: { type: Object, required: true } })
const step = props.step

const shortTool = computed(() => step.tool.split('.').pop())

const argsText = computed(() => {
  const entries = Object.entries(step.args || {})
  if (!entries.length) return ''
  const s = entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')
  return s.length > 72 ? s.slice(0, 72) + '…' : s
})

const riskClass = computed(() => {
  if (step.status === 'denied' || step.status === 'skipped') return 'deny'
  return step.verification?.decision.risk || step.risk || 'low'
})

const statusText = computed(() => {
  const risk = { low: '低危', medium: '中危', high: '高危' }[
    step.verification?.decision.risk || step.risk] || ''
  switch (step.status) {
    case 'verifying': return '校验中'
    case 'reviewing': return '审查中'
    case 'waiting':   return `${risk} · 等待确认`
    case 'running':   return '执行中'
    case 'done':      return step.autoAllowed ? `${risk} · 自动放行` : `${risk} · 已执行`
    case 'denied':    return `已拒绝`
    case 'skipped':   return '已跳过'
    default: return ''
  }
})

const durationText = computed(() => {
  const ms = step.durationMs
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

const preview = computed(() => {
  if (step.status !== 'done' || !step.output) return ''
  const lines = step.output.split('\n').filter(l => l.trim())
  const head = lines.slice(0, 2).join('  ')
  return lines.length > 2 ? head + '  …' : head
})
</script>

<style scoped>
.step-block { margin: 3px 0; }

.step-header {
  display: flex; align-items: center; gap: 7px;
  padding: 4px 6px; border-radius: 5px;
  font-size: 13px; cursor: pointer; user-select: none;
  transition: background 0.1s;
}
.step-header:hover { background: #0d1117; }

.bullet { font-size: 9px; flex-shrink: 0; line-height: 1; }
.bullet.low    { color: #3fb950; }
.bullet.medium { color: #d29922; }
.bullet.high   { color: #f85149; }
.bullet.deny   { color: #f85149; }

.tool-name {
  color: #79c0ff; font-size: 12px; font-weight: 600;
  font-family: ui-monospace, Consolas, monospace; flex-shrink: 0;
}
.args {
  color: #6e7681; font-size: 11px;
  font-family: ui-monospace, Consolas, monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.spacer { flex: 1; }

.status-badge { font-size: 11px; flex-shrink: 0; color: #6e7681; }
.status-badge.done    { color: #3fb950; }
.status-badge.denied,
.status-badge.skipped { color: #f85149; }
.status-badge.waiting { color: #d29922; }
.status-badge.verifying,
.status-badge.reviewing { color: #58a6ff; }

.duration { font-size: 11px; color: #484f58; font-family: ui-monospace, Consolas, monospace; flex-shrink: 0; }
.chevron { font-size: 13px; color: #484f58; flex-shrink: 0; transition: transform 0.15s; }
.chevron.open { transform: rotate(90deg); }

.tree-indent {
  color: #30363d; font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; flex-shrink: 0; width: 22px;
}

.preview-line {
  display: flex; align-items: baseline; gap: 6px;
  padding: 1px 6px 1px 16px; cursor: pointer;
}
.preview-text {
  font-size: 11px; color: #484f58; font-family: ui-monospace, Consolas, monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: color 0.1s;
}
.preview-line:hover .preview-text { color: #8b949e; }

.step-body { padding: 2px 6px 6px 16px; }

.guard-row { display: flex; align-items: baseline; gap: 6px; padding: 1px 0; }
.guard-label {
  color: #6e7681; font-size: 11px; font-weight: 600; flex-shrink: 0; width: 52px;
}
.guard-text { font-size: 12px; color: #8b949e; }
.guard-text.muted { color: #484f58; }

.output-header { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.output-block {
  margin: 3px 0 0 22px; padding: 8px 10px;
  background: #010409; border: 1px solid #1e2430; border-radius: 6px;
  font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  color: #8b949e; white-space: pre-wrap; word-break: break-all;
  max-height: 280px; overflow-y: auto; line-height: 1.5;
}
</style>
