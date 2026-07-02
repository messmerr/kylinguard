<template>
  <div class="layout">
    <Sidebar v-if="showSidebar" />
    <main class="main">
      <div class="topbar">
        <el-button text size="small" @click="showSidebar = !showSidebar">☰</el-button>
        <span class="top-title">{{ activeTitle }}</span>
        <el-button text size="small" class="panel-toggle"
                   @click="showPanel = !showPanel">📊</el-button>
      </div>

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

            <ConfirmCard v-else-if="it.kind === 'confirm' && !it.hidden" :card="it" />

            <el-alert v-else-if="it.kind === 'fatal'" type="error"
                      :closable="false" :title="it.error" class="fatal" />
          </template>

          <div v-if="running && !hasStreaming" class="trace-line dim shimmer">
            ⏳ Agent 处理中…
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
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import ConfirmCard from './components/ConfirmCard.vue'
import MarkdownText from './components/MarkdownText.vue'
import Sidebar from './components/Sidebar.vue'
import StatusPanel from './components/StatusPanel.vue'
import TraceStep from './components/TraceStep.vue'
import {
  activeId, items, refreshSessions, running, sendMessage, sessions,
} from './composables/useChat.js'

const input = ref('')
const chatRef = ref(null)
const showSidebar = ref(true)
const showPanel = ref(true)

const activeTitle = computed(() =>
  sessions.value.find((s) => s.id === activeId.value)?.title || '新对话')

const hasStreaming = computed(() =>
  items.value.some((it) => it.streaming))

const ageText = (age) => (age < 3 ? '刚刚' : `${Math.round(age)} 秒前`)

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

onMounted(refreshSessions)
</script>

<style>
html, body, #app { height: 100%; margin: 0; background: #0d1117;
  color: #e6edf3; font-family: -apple-system, "Segoe UI", "Microsoft YaHei",
  sans-serif; }
.layout { display: flex; height: 100%; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.topbar { display: flex; align-items: center; gap: 8px; padding: 8px 14px;
  border-bottom: 1px solid #21262d; }
.top-title { font-size: 13px; color: #c9d1d9; flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }

.chat { flex: 1; overflow-y: auto; }
.chat-inner { max-width: 800px; margin: 0 auto; padding: 20px 24px 30px; }

.welcome { text-align: center; margin-top: 18vh; }
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
.shimmer { animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.45; } }
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
