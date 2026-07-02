<template>
  <el-container class="page">
    <el-header class="header">
      <span class="logo">🛡 麒盾 KylinGuard</span>
      <span class="subtitle">面向麒麟操作系统的安全智能运维 Agent</span>
    </el-header>

    <el-main class="chat" ref="chatRef">
      <template v-for="(it, i) in items" :key="i">
        <!-- 用户消息（主视觉） -->
        <div v-if="it.kind === 'user'" class="bubble user">{{ it.text }}</div>

        <!-- 感知：一行细字，点开看快照详情 -->
        <div v-else-if="it.kind === 'snapshot'" class="trace">
          <div class="trace-line clickable" @click="it.expanded = !it.expanded">
            <span class="dot hollow"></span>
            已感知系统状态<span class="dim"> · {{ ageText(it.age) }}采集 · 点击{{ it.expanded ? '收起' : '查看' }}</span>
          </div>
          <div v-if="it.expanded" class="detail">
            <div v-for="(v, k) in it.snapshot" :key="k" class="detail-block">
              <div class="detail-title">{{ k }}</div>
              <pre class="mono">{{ v }}</pre>
            </div>
          </div>
        </div>

        <!-- 规划思路：灰色小字 -->
        <div v-else-if="it.kind === 'thought'" class="trace">
          <div class="trace-line thought">💭 {{ it.text }}</div>
        </div>

        <!-- 步骤行：色点 + 工具 + 目的 + 状态，点开看三道闸与输出 -->
        <div v-else-if="it.kind === 'step'" class="trace">
          <div class="trace-line clickable" @click="it.expanded = !it.expanded">
            <span class="dot" :class="riskClass(it)"></span>
            <code class="mono tool">{{ shortTool(it.tool) }}</code>
            <span class="purpose">{{ it.purpose }}</span>
            <span class="status" :class="it.status">{{ statusText(it) }}</span>
          </div>
          <div v-if="it.expanded" class="detail">
            <template v-if="it.verification">
              <p>规则引擎：{{ it.verification.rule.reason }}</p>
              <p>LLM 审查员：{{ it.verification.review.reason }}</p>
              <p>门控结论：{{ it.verification.decision.reason }}</p>
            </template>
            <p v-else class="dim">校验中…</p>
            <template v-if="it.output != null">
              <div class="detail-title">执行输出</div>
              <pre class="mono">{{ it.output }}</pre>
            </template>
          </div>
        </div>

        <!-- 确认卡：仅中高危出现，处理后自动收起 -->
        <el-card v-else-if="it.kind === 'confirm' && !it.hidden"
                 class="card confirm" shadow="never">
          <template #header>⚠ 待管理员确认（{{ actionLabel(it.decision.action) }}）</template>
          <p><code class="mono">{{ it.step.tool }}</code> {{ JSON.stringify(it.step.arguments) }}</p>
          <p>{{ it.step.purpose }} —— {{ it.decision.reason }}</p>
          <div>
            <el-button type="danger" size="small"
                       @click="confirmStep(it, true)">批准执行</el-button>
            <el-button size="small" @click="confirmStep(it, false)">拒绝</el-button>
          </div>
        </el-card>

        <!-- 最终结论（主视觉） -->
        <div v-else-if="it.kind === 'final'"
             class="bubble agent" :class="{ aborted: it.aborted }">
          {{ it.answer }}
        </div>

        <!-- 致命错误 -->
        <el-alert v-else-if="it.kind === 'fatal'" type="error" :closable="false"
                  :title="it.error" class="card" />
      </template>
      <div v-if="running" class="trace-line dim">⏳ Agent 处理中…</div>
    </el-main>

    <el-footer class="footer">
      <el-input v-model="input" placeholder="用自然语言下达运维指令，如：看看现在系统负载怎么样"
                :disabled="running" @keyup.enter="send">
        <template #append>
          <el-button type="primary" :loading="running" @click="send">发送</el-button>
        </template>
      </el-input>
    </el-footer>
  </el-container>
</template>

<script setup>
import { ElMessageBox } from 'element-plus'
import { nextTick, reactive, ref } from 'vue'

const input = ref('')
const running = ref(false)
const items = ref([])
const chatRef = ref(null)
// step_id → 步骤行对象；confirm_id → 确认卡对象（跨事件更新状态用）
let stepsById = {}
let confirmsById = {}

const riskLabelMap = { low: '低危', medium: '中危', high: '高危' }
const actionLabel = (a) => ({
  auto: '自动放行', confirm: '需确认', double_confirm: '需二次确认', deny: '已拒绝',
}[a] || a)

const shortTool = (t) => t.split('.').pop()
const ageText = (age) => (age < 3 ? '刚刚' : `${Math.round(age)} 秒前`)

function riskClass(step) {
  const risk = step.verification?.decision.risk || step.risk
  if (step.status === 'denied' || step.status === 'skipped') return 'risk-deny'
  return `risk-${risk}`
}

function statusText(step) {
  const risk = riskLabelMap[step.verification?.decision.risk || step.risk] || ''
  switch (step.status) {
    case 'verifying': return '校验中…'
    case 'waiting': return `⏳ ${risk}，等待确认`
    case 'running': return '执行中…'
    case 'done': return step.autoAllowed ? '✓ 自动放行' : '✓ 已执行'
    case 'denied': return `✗ 已拒绝：${step.denyReason}`
    case 'skipped': return '✗ 未获批准，已跳过'
    default: return ''
  }
}

async function scrollToBottom() {
  await nextTick()
  const el = chatRef.value?.$el
  if (el) el.scrollTop = el.scrollHeight
}

function push(item) {
  const r = reactive(item)
  items.value.push(r)
  scrollToBottom()
  return r
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'snapshot':
      push({ kind: 'snapshot', snapshot: ev.snapshot,
             age: ev.collected_ago_seconds ?? 0, expanded: false })
      break
    case 'plan':
      if (ev.steps.length && ev.thought) push({ kind: 'thought', text: ev.thought })
      for (const s of ev.steps) {
        stepsById[s.step_id] = push({
          kind: 'step', tool: s.tool, purpose: s.purpose, risk: s.risk,
          status: 'verifying', verification: null, output: null,
          autoAllowed: false, denyReason: '', expanded: false,
        })
      }
      break
    case 'verification': {
      const step = stepsById[ev.step_id]
      if (!step) break
      step.verification = { rule: ev.rule, review: ev.review, decision: ev.decision }
      if (ev.decision.action === 'deny') {
        step.status = 'denied'
        step.denyReason = ev.decision.reason
      } else if (ev.decision.action === 'auto') {
        step.status = 'running'
        step.autoAllowed = true
      } else {
        step.status = 'waiting'
      }
      break
    }
    case 'confirm_request':
      confirmsById[ev.confirm_id] = push({
        kind: 'confirm', confirmId: ev.confirm_id, stepId: ev.step_id,
        step: ev.step, decision: ev.decision, hidden: false,
      })
      break
    case 'confirm_result': {
      const card = confirmsById[ev.confirm_id]
      if (card) card.hidden = true
      const step = stepsById[ev.step_id]
      if (step) step.status = ev.approved ? 'running' : 'skipped'
      break
    }
    case 'execution': {
      const step = stepsById[ev.step_id]
      if (step) {
        step.status = 'done'
        step.output = ev.output
      }
      break
    }
    case 'final_answer':
      push({ kind: 'final', answer: ev.answer, aborted: ev.aborted })
      break
    case 'fatal':
      push({ kind: 'fatal', error: ev.error })
      break
  }
}

async function confirmStep(card, approved) {
  if (approved && card.decision.action === 'double_confirm') {
    try {
      const { value } = await ElMessageBox.prompt(
        '高危操作！请输入「确认执行」以二次确认', '二次确认',
        { confirmButtonText: '执行', cancelButtonText: '取消' })
      if (value !== '确认执行') return
    } catch { return }
  }
  await fetch('/api/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_id: card.confirmId, approved }),
  })
}

async function send() {
  const message = input.value.trim()
  if (!message || running.value) return
  input.value = ''
  running.value = true
  stepsById = {}
  confirmsById = {}
  push({ kind: 'user', text: message })
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        if (line.startsWith('data: ')) handleEvent(JSON.parse(line.slice(6)))
      }
    }
  } catch (e) {
    push({ kind: 'fatal', error: `连接中断：${e.message}` })
  } finally {
    running.value = false
  }
}
</script>

<style>
html, body, #app { height: 100%; margin: 0; background: #0d1117; }
.page { height: 100%; }
.header { display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid #21262d; color: #e6edf3; }
.logo { font-size: 18px; font-weight: 700; }
.subtitle { font-size: 12px; color: #8b949e; }
.chat { overflow-y: auto; }

/* 主视觉：对话气泡 */
.bubble { max-width: 640px; padding: 10px 14px; border-radius: 10px;
  margin: 12px 0; white-space: pre-wrap; font-size: 14px; line-height: 1.6; }
.bubble.user { background: #1f6feb; color: #fff; margin-left: auto; }
.bubble.agent { background: #21262d; color: #e6edf3; }
.bubble.aborted { border: 1px solid #f85149; }

/* 从属视觉：紧凑过程行 */
.trace { max-width: 720px; }
.trace-line { display: flex; align-items: baseline; gap: 8px;
  padding: 3px 8px; font-size: 13px; color: #c9d1d9; line-height: 1.5; }
.trace-line.clickable { cursor: pointer; border-radius: 6px; }
.trace-line.clickable:hover { background: #161b22; }
.trace-line.thought { color: #8b949e; font-size: 12px; }
.dim { color: #8b949e; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  align-self: center; }
.dot.hollow { border: 1.5px solid #8b949e; background: transparent; }
.dot.risk-low { background: #3fb950; }
.dot.risk-medium { background: #d29922; }
.dot.risk-high { background: #f85149; }
.dot.risk-deny { background: #f85149; }
.tool { color: #79c0ff; font-size: 12px; }
.purpose { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { margin-left: auto; flex-shrink: 0; font-size: 12px; color: #8b949e; }
.status.done { color: #3fb950; }
.status.denied, .status.skipped { color: #f85149; }
.status.waiting { color: #d29922; }

/* 展开详情 */
.detail { margin: 2px 0 6px 24px; padding: 8px 10px; background: #161b22;
  border-radius: 6px; font-size: 12px; color: #c9d1d9; }
.detail p { margin: 2px 0; }
.detail-title { color: #8b949e; margin-top: 6px; font-size: 11px; }
.detail-block { margin-bottom: 6px; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 2px 0; }
.detail .mono { max-height: 300px; overflow-y: auto; }

/* 确认卡（醒目保留） */
.card { max-width: 720px; margin: 10px 0; }
.card.confirm { border-color: #d29922; }
.footer { padding: 12px 16px; border-top: 1px solid #21262d; }
</style>
