<template>
  <div class="step-block">
    <div class="step-line" @click="step.expanded = !step.expanded">
      <span class="dot" :class="riskClass"></span>
      <code class="tool">{{ shortTool }}</code>
      <code v-if="argsText" class="args">{{ argsText }}</code>
      <span class="purpose">{{ step.purpose }}</span>
      <span class="status" :class="step.status">
        {{ statusText }}<template v-if="step.durationMs != null"> · {{ durationText }}</template>
      </span>
    </div>

    <!-- 输出预览：完成后默认露出首行，不必点开 -->
    <pre v-if="preview && !step.expanded" class="preview mono"
         @click="step.expanded = true">{{ preview }}</pre>

    <div v-if="step.expanded" class="detail">
      <template v-if="step.verification">
        <p><span class="label">规则引擎</span>{{ step.verification.rule.reason }}</p>
        <p><span class="label">LLM 审查员</span>{{ step.verification.review.reason }}</p>
        <p><span class="label">门控结论</span>{{ step.verification.decision.reason }}</p>
      </template>
      <p v-else class="label">三道闸校验中…</p>
      <template v-if="step.output != null">
        <div class="label out-label">完整输出</div>
        <pre class="mono full-output">{{ step.output }}</pre>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ step: { type: Object, required: true } })
const step = props.step

const riskLabelMap = { low: '低危', medium: '中危', high: '高危' }
const shortTool = computed(() => step.tool.split('.').pop())

const argsText = computed(() => {
  const entries = Object.entries(step.args || {})
  if (!entries.length) return ''
  const s = entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')
  return s.length > 60 ? s.slice(0, 60) + '…' : s
})

const riskClass = computed(() => {
  if (step.status === 'denied' || step.status === 'skipped') return 'risk-deny'
  return `risk-${step.verification?.decision.risk || step.risk}`
})

const statusText = computed(() => {
  const risk = riskLabelMap[step.verification?.decision.risk || step.risk] || ''
  switch (step.status) {
    case 'verifying': return '规则引擎校验中…'
    case 'reviewing': return '独立 LLM 审查中…'
    case 'waiting': return `⏳ ${risk}，等待确认`
    case 'running': return '执行中…'
    case 'done': return step.autoAllowed ? '✓ 自动放行' : '✓ 已执行'
    case 'denied': return `✗ 已拒绝：${step.denyReason}`
    case 'skipped': return '✗ 未获批准，已跳过'
    default: return ''
  }
})

const durationText = computed(() => {
  const ms = step.durationMs
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
})

const preview = computed(() => {
  if (step.status !== 'done' || !step.output) return ''
  const lines = step.output.split('\n').filter((l) => l.trim())
  const head = lines.slice(0, 2).join('\n')
  return lines.length > 2 ? head + '\n…' : head
})
</script>

<style scoped>
.step-block { margin: 2px 0; }
.step-line { display: flex; align-items: baseline; gap: 8px;
  padding: 4px 8px; font-size: 13px; color: #c9d1d9; cursor: pointer;
  border-radius: 6px; line-height: 1.5; }
.step-line:hover { background: #161b22; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  align-self: center; }
.dot.risk-low { background: #3fb950; }
.dot.risk-medium { background: #d29922; }
.dot.risk-high, .dot.risk-deny { background: #f85149; }
.tool { color: #79c0ff; font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.args { color: #8b949e; font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.purpose { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { margin-left: auto; flex-shrink: 0; font-size: 12px; color: #8b949e; }
.status.done { color: #3fb950; }
.status.denied, .status.skipped { color: #f85149; }
.status.waiting { color: #d29922; }
.status.verifying, .status.reviewing { color: #58a6ff; }
.preview { margin: 0 0 2px 24px; padding: 6px 10px; background: #10141a;
  border-left: 2px solid #21262d; border-radius: 0 6px 6px 0;
  color: #8b949e; cursor: pointer; }
.preview:hover { color: #c9d1d9; }
.detail { margin: 2px 0 8px 24px; padding: 8px 12px; background: #161b22;
  border-radius: 8px; font-size: 12px; color: #c9d1d9; }
.detail p { margin: 3px 0; }
.label { color: #8b949e; margin-right: 8px; }
.out-label { margin-top: 8px; }
.full-output { max-height: 320px; overflow-y: auto; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-all; }
</style>
