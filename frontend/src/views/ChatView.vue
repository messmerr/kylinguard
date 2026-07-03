<template>
  <div class="chat-layout">
    <Sidebar v-if="showSidebar" />
    <main class="main">
      <div class="chat" ref="chatRef">
        <div class="chat-inner">
          <div v-if="!items.length" class="welcome">
            <div class="welcome-logo">🛡</div>
            <div class="welcome-title">麒盾 KylinGuard</div>
            <div class="welcome-sub">用自然语言下达运维指令，五阶段安全流水线全程护航</div>
          </div>

          <template v-for="(it, i) in items" :key="i">
            <div v-if="it.kind === 'user'" class="bubble user">{{ it.text }}</div>

            <div v-else-if="it.kind === 'snapshot'" class="trace-line clickable"
                 @click="it.expanded = !it.expanded">
              <span class="dot hollow"></span>
              已感知系统状态<span class="dim"> · {{ ageText(it.age) }}采集</span>
            </div>
            <div v-if="it.kind === 'snapshot' && it.expanded" class="snap-detail">
              <div v-for="(v, k) in it.snapshot" :key="k">
                <div class="snap-key">{{ k }}</div>
                <pre class="mono">{{ v }}</pre>
              </div>
            </div>

            <div v-else-if="it.kind === 'assistant'"
                 class="assistant" :class="[it.role, { aborted: it.aborted }]">
              <MarkdownText :text="it.text" />
              <span v-if="it.streaming" class="cursor">▍</span>
            </div>

            <TraceStep v-else-if="it.kind === 'step'" :step="it" />

            <div v-else-if="it.kind === 'intent'" class="intent-card"
                 @click="it.expanded = !it.expanded">
              <div class="intent-head">
                <span class="intent-title">安全意图校验器已拒绝</span>
                <code class="intent-rule">{{ it.decision?.matched_rule || 'policy' }}</code>
              </div>
              <div class="intent-reason">{{ it.decision?.reason }}</div>
              <pre v-if="it.expanded" class="mono intent-detail">{{
                JSON.stringify(it.decision, null, 2) }}</pre>
            </div>

            <ConfirmCard v-else-if="it.kind === 'confirm' && !it.hidden" :card="it" />

            <el-alert v-else-if="it.kind === 'fatal'" type="error"
                      :closable="false" :title="it.error" class="fatal" />
          </template>

          <div v-if="running && !hasStreaming" class="phase-bar">
            <span class="phase-spinner"></span>
            <span class="phase-text">{{ phaseText }}</span>
            <span class="phase-timer">{{ timer }}s</span>
          </div>
        </div>
      </div>

      <div class="composer">
        <div class="composer-box">
          <el-input v-model="input" type="textarea" :rows="1" autosize
                    resize="none" placeholder="用自然语言下达运维指令…"
                    :disabled="running" @keydown.enter.exact.prevent="submit" />
          <el-button class="send-btn" type="primary" circle
                     :loading="running" @click="submit">↑</el-button>
        </div>
        <div class="composer-hint">Enter 发送 · 中高危操作将请求确认 · 全程审计留痕</div>
      </div>
    </main>
    <StatusPanel v-if="showPanel" />
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import ConfirmCard from '../components/ConfirmCard.vue'
import MarkdownText from '../components/MarkdownText.vue'
import Sidebar from '../components/Sidebar.vue'
import StatusPanel from '../components/StatusPanel.vue'
import TraceStep from '../components/TraceStep.vue'
import { items, phase, running, sendMessage } from '../composables/useChat.js'

defineProps({
  showSidebar: { type: Boolean, default: true },
  showPanel: { type: Boolean, default: true },
})

const input = ref('')
const chatRef = ref(null)

const hasStreaming = computed(() => items.value.some((it) => it.streaming))
const ageText = (age) => (age < 3 ? '刚刚' : `${Math.round(age)} 秒前`)

// 阶段指示文案：让"处理中"变成用户可感的具体环节
const phaseText = computed(() => {
  const p = phase.value
  if (!p) return '正在准备…'
  if (p.name === 'planning') return '规划模型正在分析系统状态、拟定执行计划…'
  if (p.name === 'reviewing') {
    const t = p.tool ? p.tool.split('.').pop() : ''
    return `独立 LLM 审查员正在校验「${t}」的安全性与意图一致性…`
  }
  return '处理中…'
})

// 秒表：任务进行期间每 100ms 递增，让等待有进度感
const timer = ref('0.0')
let timerId = null
let startAt = 0
watch(running, (on) => {
  if (on) {
    startAt = performance.now()
    timer.value = '0.0'
    timerId = setInterval(() => {
      timer.value = ((performance.now() - startAt) / 1000).toFixed(1)
    }, 100)
  } else if (timerId) {
    clearInterval(timerId)
    timerId = null
  }
})
onUnmounted(() => timerId && clearInterval(timerId))

function scrollToBottom() {
  nextTick(() => {
    const el = chatRef.value
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 260) {
      el.scrollTop = el.scrollHeight
    }
  })
}

watch(() => items.value.map((it) => it.kind === 'assistant' ? it.text.length : it.status).join(),
      scrollToBottom)
watch(() => items.value.length, () => {
  nextTick(() => {
    const el = chatRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
})

async function submit() {
  const text = input.value.trim()
  if (!text) return
  input.value = ''
  await sendMessage(text, { onUpdate: scrollToBottom })
}
</script>

<style scoped>
.chat-layout { display: flex; flex: 1; min-height: 0; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat { flex: 1; overflow-y: auto; }
.chat-inner { max-width: 800px; margin: 0 auto; padding: 20px 24px 30px; }

.welcome { text-align: center; margin-top: 16vh; }
.welcome-logo { font-size: 44px; }
.welcome-title { font-size: 22px; font-weight: 700; margin-top: 10px; }
.welcome-sub { color: #8b949e; font-size: 13px; margin-top: 6px; }

.bubble.user { max-width: 620px; margin: 16px 0 16px auto; width: fit-content;
  background: #1f6feb; color: #fff; padding: 10px 14px; border-radius: 12px;
  white-space: pre-wrap; font-size: 14px; line-height: 1.6; }

.assistant { margin: 10px 0; font-size: 14px; color: #e6edf3; }
.assistant.thinking { color: #8b949e; font-size: 13px;
  border-left: 2px solid #21262d; padding-left: 12px; margin-left: 4px; }
.assistant.aborted { border: 1px solid #f85149; border-radius: 8px;
  padding: 8px 12px; }
.cursor { color: #58a6ff; animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.trace-line { display: flex; align-items: baseline; gap: 8px; padding: 4px 8px;
  font-size: 13px; color: #c9d1d9; border-radius: 6px; line-height: 1.5; }
.trace-line.clickable { cursor: pointer; }
.trace-line.clickable:hover { background: #161b22; }
.dim { color: #8b949e; }
.phase-bar { display: flex; align-items: center; gap: 10px; margin: 10px 0;
  padding: 8px 14px; background: #161b22; border: 1px solid #21262d;
  border-radius: 10px; font-size: 13px; color: #c9d1d9; }
.phase-spinner { width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid #30363d; border-top-color: #58a6ff; flex-shrink: 0;
  animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.phase-text { flex: 1; }
.phase-timer { color: #8b949e; font-size: 12px;
  font-family: ui-monospace, Consolas, monospace; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  align-self: center; }
.dot.hollow { border: 1.5px solid #8b949e; background: transparent; }
.snap-detail { margin: 2px 0 8px 24px; padding: 8px 12px; background: #161b22;
  border-radius: 8px; max-height: 300px; overflow-y: auto; }
.snap-key { color: #8b949e; font-size: 11px; margin-top: 6px; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-all;
  margin: 2px 0; color: #c9d1d9; }
.fatal { margin: 10px 0; }
.intent-card { margin: 8px 0 8px 24px; padding: 10px 12px;
  border: 1px solid #f85149; background: #1f1214; border-radius: 8px;
  cursor: pointer; }
.intent-head { display: flex; align-items: center; gap: 10px; }
.intent-title { color: #ff7b72; font-weight: 700; font-size: 13px; }
.intent-rule { margin-left: auto; max-width: 240px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; color: #8b949e;
  font-size: 11px; }
.intent-reason { color: #e6edf3; font-size: 13px; margin-top: 4px; }
.intent-detail { margin-top: 8px; color: #c9d1d9; }

.composer { padding: 10px 24px 14px; }
.composer-box { max-width: 800px; margin: 0 auto; position: relative;
  background: #161b22; border: 1px solid #30363d; border-radius: 14px;
  padding: 8px 52px 8px 12px; transition: border-color 0.2s; }
.composer-box:focus-within { border-color: #58a6ff; }
.composer-box :deep(.el-textarea__inner) { background: transparent;
  border: none; box-shadow: none; color: #e6edf3; font-size: 14px;
  max-height: 160px; }
.send-btn { position: absolute; right: 8px; bottom: 8px; }
.composer-hint { max-width: 800px; margin: 6px auto 0; text-align: center;
  font-size: 11px; color: #484f58; }
</style>
