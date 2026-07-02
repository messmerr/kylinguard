<template>
  <el-container class="page">
    <el-header class="header">
      <span class="logo">🛡 麒盾 KylinGuard</span>
      <span class="subtitle">面向麒麟操作系统的安全智能运维 Agent</span>
    </el-header>

    <el-main class="chat" ref="chatRef">
      <template v-for="(ev, i) in events" :key="i">
        <!-- 用户消息 -->
        <div v-if="ev.type === 'user'" class="bubble user">{{ ev.text }}</div>

        <!-- ① 感知快照 -->
        <el-card v-else-if="ev.type === 'snapshot'" class="card" shadow="never">
          <template #header>① 感知 · 系统快照</template>
          <el-collapse>
            <el-collapse-item v-for="(v, k) in ev.snapshot" :key="k" :title="k">
              <pre class="mono">{{ v }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-card>

        <!-- ② 执行计划 -->
        <el-card v-else-if="ev.type === 'plan' && ev.steps.length" class="card" shadow="never">
          <template #header>② 规划 · 执行计划（第 {{ ev.round + 1 }} 轮）</template>
          <p class="thought">{{ ev.thought }}</p>
          <div v-for="(s, j) in ev.steps" :key="j" class="step">
            <el-tag :type="riskColor(s.risk)" size="small">{{ riskLabel(s.risk) }}</el-tag>
            <code class="mono">{{ s.tool }}</code>
            <span>{{ s.purpose }}</span>
          </div>
        </el-card>

        <!-- ③ 校验判定 -->
        <el-card v-else-if="ev.type === 'verification'" class="card" shadow="never">
          <template #header>
            ③ 校验 · {{ ev.step.tool }}
            <el-tag :type="ev.decision.action === 'deny' ? 'danger' : riskColor(ev.decision.risk)" size="small">
              {{ actionLabel(ev.decision.action) }}
            </el-tag>
          </template>
          <p>规则引擎：{{ ev.rule.reason }}</p>
          <p>LLM 审查员：{{ ev.review.reason }}</p>
          <p>门控结论：{{ ev.decision.reason }}</p>
        </el-card>

        <!-- 确认请求 -->
        <el-card v-else-if="ev.type === 'confirm_request'" class="card confirm" shadow="never">
          <template #header>⚠ 待管理员确认（{{ actionLabel(ev.decision.action) }}）</template>
          <p><code class="mono">{{ ev.step.tool }}</code> {{ JSON.stringify(ev.step.arguments) }}</p>
          <p>{{ ev.step.purpose }} —— {{ ev.decision.reason }}</p>
          <div v-if="!resolved[ev.confirm_id]">
            <el-button type="danger" size="small"
                       @click="confirmStep(ev, true)">批准执行</el-button>
            <el-button size="small" @click="confirmStep(ev, false)">拒绝</el-button>
          </div>
          <el-tag v-else size="small">已处理</el-tag>
        </el-card>

        <!-- 确认结果 -->
        <div v-else-if="ev.type === 'confirm_result'" class="notice">
          {{ ev.approved ? '✔ 管理员已批准' : '✘ 已拒绝或超时，步骤跳过' }}
        </div>

        <!-- ④ 执行结果 -->
        <el-card v-else-if="ev.type === 'execution'" class="card" shadow="never">
          <template #header>④ 执行 · {{ ev.step.tool }}</template>
          <el-collapse>
            <el-collapse-item title="输出详情">
              <pre class="mono">{{ ev.output }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-card>

        <!-- 最终结论 -->
        <div v-else-if="ev.type === 'final_answer'"
             class="bubble agent" :class="{ aborted: ev.aborted }">
          {{ ev.answer }}
        </div>

        <!-- 致命错误 -->
        <el-alert v-else-if="ev.type === 'fatal'" type="error" :closable="false"
                  :title="ev.error" class="card" />
      </template>
      <div v-if="running" class="notice">⏳ Agent 处理中…</div>
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
const events = ref([])
const resolved = reactive({})
const chatRef = ref(null)

const riskColor = (r) => ({ low: 'success', medium: 'warning', high: 'danger' }[r] || 'info')
const riskLabel = (r) => ({ low: '低危', medium: '中危', high: '高危' }[r] || r)
const actionLabel = (a) => ({
  auto: '自动放行', confirm: '需确认', double_confirm: '需二次确认', deny: '已拒绝',
}[a] || a)

async function scrollToBottom() {
  await nextTick()
  const el = chatRef.value?.$el
  if (el) el.scrollTop = el.scrollHeight
}

function pushEvent(ev) {
  events.value.push(ev)
  scrollToBottom()
}

async function confirmStep(ev, approved) {
  if (approved && ev.decision.action === 'double_confirm') {
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
    body: JSON.stringify({ confirm_id: ev.confirm_id, approved }),
  })
  resolved[ev.confirm_id] = true
}

async function send() {
  const message = input.value.trim()
  if (!message || running.value) return
  input.value = ''
  running.value = true
  pushEvent({ type: 'user', text: message })
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
        if (line.startsWith('data: ')) {
          const ev = JSON.parse(line.slice(6))
          if (ev.type !== 'done' && ev.type !== 'user_query') pushEvent(ev)
        }
      }
    }
  } catch (e) {
    pushEvent({ type: 'fatal', error: `连接中断：${e.message}` })
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
.card { max-width: 780px; margin: 10px 0; }
.card.confirm { border-color: #d29922; }
.bubble { max-width: 640px; padding: 10px 14px; border-radius: 10px;
  margin: 10px 0; white-space: pre-wrap; }
.bubble.user { background: #1f6feb; color: #fff; margin-left: auto; }
.bubble.agent { background: #21262d; color: #e6edf3; }
.bubble.aborted { border: 1px solid #f85149; }
.notice { color: #8b949e; font-size: 13px; margin: 6px 0; }
.thought { color: #8b949e; font-size: 13px; }
.step { display: flex; gap: 8px; align-items: center; margin: 6px 0;
  color: #e6edf3; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.footer { padding: 12px 16px; border-top: 1px solid #21262d; }
</style>
