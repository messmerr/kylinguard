<template>
  <div class="chat-layout">
    <main class="main">
      <div ref="chatRef" class="chat">
        <div class="chat-inner" :class="{ empty: !items.length }">
          <section v-if="!items.length" class="welcome" aria-labelledby="welcome-title">
            <span class="welcome-mark"><KgLogo :size="28" /></span>
            <h1 id="welcome-title">今天要处理什么？</h1>
            <p>描述要检查或执行的任务，执行前会检查风险。</p>

            <div class="welcome-hints">
              <button v-for="hint in WELCOME_HINTS" :key="hint.text" type="button"
                      class="hint" :disabled="running" @click="sendHint(hint.text)">
                <span class="hint-icon"><KgIcon :name="hint.icon" :size="16" /></span>
                <span class="hint-copy">
                  <strong>{{ hint.title }}</strong>
                  <small>{{ hint.description }}</small>
                </span>
                <KgIcon name="chevron" :size="14" />
              </button>
            </div>
          </section>

          <template v-for="(it, i) in items" :key="i">
            <article v-if="it.kind === 'user'" class="user-prompt">
              <span class="user-chevron">›</span>
              <div class="user-text">{{ it.text }}</div>
            </article>

            <section v-else-if="it.kind === 'snapshot'" class="snapshot-block">
              <button type="button" class="record-line" :aria-expanded="it.expanded"
                      @click="it.expanded = !it.expanded">
                <span class="record-node"><KgIcon name="activity" :size="14" /></span>
                <span class="record-title">已读取系统状态</span>
                <span class="record-meta">{{ ageText(it.age) }}采集</span>
                <KgIcon name="chevron" :size="14" class="chevron"
                        :class="{ open: it.expanded }" />
              </button>
              <div v-if="it.expanded" class="snapshot-detail">
                <div v-for="(value, key) in it.snapshot" :key="key" class="snapshot-item">
                  <div class="snapshot-key">{{ key }}</div>
                  <pre>{{ value }}</pre>
                </div>
              </div>
            </section>

            <article v-else-if="it.kind === 'assistant'"
                     class="assistant" :class="[it.role, { aborted: it.aborted }]">
              <div v-if="it.role === 'thinking'" class="assistant-state">
                <span v-if="it.streaming" class="state-dot"></span>
                <span>{{ it.streaming ? '正在分析' : '分析过程' }}</span>
              </div>
              <MarkdownText :text="it.text" />
              <span v-if="it.streaming" class="cursor">▍</span>
              <button v-if="it.isReport && !it.streaming" type="button"
                      class="inline-action" @click="downloadReport(it.text)">
                <KgIcon name="download" :size="14" />下载 Markdown
              </button>
            </article>

            <TraceStep v-else-if="it.kind === 'step'" :step="it" />

            <article v-else-if="it.kind === 'intent'" class="intent-card" role="button"
                     tabindex="0" :aria-expanded="it.expanded"
                     @click="it.expanded = !it.expanded"
                     @keydown.enter="it.expanded = !it.expanded"
                     @keydown.space.prevent="it.expanded = !it.expanded">
              <div class="intent-head">
                <span class="intent-icon"><KgIcon name="lock" :size="16" /></span>
                <strong>请求已阻止</strong>
                <code>{{ it.decision?.matched_rule || 'policy' }}</code>
                <KgIcon name="chevron" :size="14" class="chevron"
                        :class="{ open: it.expanded }" />
              </div>
              <div class="intent-reason">{{ it.decision?.reason }}</div>
              <pre v-if="it.expanded" class="intent-detail">{{
                JSON.stringify(it.decision, null, 2) }}</pre>
            </article>

            <ConfirmCard v-else-if="it.kind === 'confirm' && !it.hidden" :card="it" />

            <el-alert v-else-if="it.kind === 'fatal'" type="error"
                      :closable="false" :title="it.error" class="fatal" />
          </template>

          <div v-if="running && !hasStreaming" class="phase-bar">
            <span class="kg-spinner" aria-hidden="true"></span>
            <span class="phase-text">{{ phaseText }}</span>
            <span class="phase-timer">{{ timer }}s</span>
          </div>
        </div>
      </div>

      <div class="composer">
        <div class="composer-shell">
          <div class="composer-box">
            <el-input v-model="input" type="textarea" :rows="1" autosize
                      resize="none" placeholder="描述运维任务…"
                      :disabled="running" @keydown.enter.exact.prevent="submit" />
            <button type="button" class="send-btn" aria-label="发送运维指令"
                    :disabled="running || !input.trim()" @click="submit">
              <span v-if="running" class="send-spinner"></span>
              <KgIcon v-else name="arrowUp" :size="16" />
            </button>
          </div>
          <div class="composer-footer">
            <span>Enter 发送 · Shift+Enter 换行</span>
            <button v-if="activeId && items.length" type="button" class="inline-action"
                    :disabled="running" @click="genReport">
              <KgIcon name="task" :size="13" />生成运维报告
            </button>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import ConfirmCard from '../components/ConfirmCard.vue'
import KgIcon from '../components/KgIcon.vue'
import KgLogo from '../components/KgLogo.vue'
import MarkdownText from '../components/MarkdownText.vue'
import TraceStep from '../components/TraceStep.vue'
import { activeId, generateReport, items, phase, running, sendMessage } from '../composables/useChat.js'

const WELCOME_HINTS = [
  { icon: 'disk', title: '检查磁盘使用', description: '只读取状态', text: '查看磁盘使用情况' },
  { icon: 'cpu', title: '查看高占用进程', description: '只读取状态', text: '列出 CPU 占用最高的进程' },
  { icon: 'activity', title: '检查失败服务', description: '只读取状态', text: '检查失败的服务' },
]

const input = ref('')
const chatRef = ref(null)

const hasStreaming = computed(() => items.value.some((item) => item.streaming))
const ageText = (age) => (age < 3 ? '刚刚' : `${Math.round(age)} 秒前`)

const phaseText = computed(() => {
  const current = phase.value
  if (!current) return '正在准备…'
  if (current.name === 'planning') return '正在分析当前状态…'
  if (current.name === 'reviewing') {
    const tool = current.tool ? current.tool.split('.').pop() : '操作'
    return `正在检查 ${tool}…`
  }
  return '处理中…'
})

const timer = ref('0.0')
let timerId = null
let startAt = 0
watch(running, (active) => {
  if (active) {
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
    const element = chatRef.value
    if (element && element.scrollHeight - element.scrollTop - element.clientHeight < 260) {
      element.scrollTop = element.scrollHeight
    }
  })
}

watch(() => items.value.map((item) => (
  item.kind === 'assistant' ? item.text.length : item.status
)).join(), scrollToBottom)
watch(() => items.value.length, () => {
  nextTick(() => {
    const element = chatRef.value
    if (element) element.scrollTop = element.scrollHeight
  })
})

async function submit() {
  const text = input.value.trim()
  if (!text) return
  input.value = ''
  await sendMessage(text, { onUpdate: scrollToBottom })
}

async function sendHint(text) {
  if (running.value) return
  await sendMessage(text, { onUpdate: scrollToBottom })
}

async function genReport() {
  await generateReport({ onUpdate: scrollToBottom })
}

function downloadReport(text) {
  const date = new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')
  const blob = new Blob([text], { type: 'text/markdown; charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `运维报告_${date}.md`
  link.click()
  URL.revokeObjectURL(link.href)
}
</script>

<style scoped>
.chat-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  background: var(--kg-bg-canvas);
}

.main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.chat {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.chat-inner {
  width: min(100%, var(--kg-thread-max));
  margin: 0 auto;
  padding: 28px 24px 40px;
}

.chat-inner.empty {
  min-height: 100%;
  display: grid;
  align-items: start;
}

.welcome {
  width: 100%;
  margin-top: clamp(56px, 13vh, 120px);
  text-align: center;
}

.welcome-mark {
  width: 44px;
  height: 44px;
  display: grid;
  margin: 0 auto var(--kg-space-4);
  place-items: center;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  color: var(--kg-accent);
  box-shadow: inset 0 1px rgb(255 255 255 / 3%);
}

.welcome h1 {
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 22px;
  font-weight: 600;
  line-height: 30px;
}

.welcome > p {
  margin: var(--kg-space-2) 0 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
}

.welcome-hints {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--kg-space-2);
  margin-top: var(--kg-space-8);
}

.hint {
  min-width: 0;
  min-height: 58px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 11px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-tertiary);
  text-align: left;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.hint:hover:not(:disabled) {
  border-color: var(--kg-border-default);
  background: var(--kg-bg-surface-2);
  color: var(--kg-accent);
}

.hint:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }

.hint-icon {
  width: 30px;
  height: 30px;
  display: grid;
  flex: none;
  place-items: center;
  border-radius: var(--kg-radius-sm);
  background: var(--kg-accent-soft);
  color: var(--kg-accent);
}

.hint-copy { min-width: 0; flex: 1; display: grid; gap: 2px; }
.hint-copy strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 500; }
.hint-copy small { color: var(--kg-text-tertiary); font-size: 12px; }

.user-prompt {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 12px 0 20px;
  padding-left: 2px;
}

.user-chevron {
  flex: none;
  color: var(--kg-accent);
  font: 18px/1 var(--kg-font-mono);
}

.user-text {
  color: var(--kg-text-primary);
  font-size: 15px;
  font-weight: 500;
  line-height: 1.6;
  white-space: pre-wrap;
}

.snapshot-block { margin: 5px 0; }

.record-line {
  width: 100%;
  min-height: 38px;
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
}

.record-line:hover { border-color: var(--kg-border-subtle); background: var(--kg-bg-surface-1); }

.record-node {
  width: 26px;
  height: 26px;
  display: grid;
  flex: none;
  place-items: center;
  border: 1px solid var(--kg-border-subtle);
  border-radius: 50%;
  color: var(--kg-success);
}

.record-title { flex: 1; font-size: 13px; }
.record-meta { color: var(--kg-text-tertiary); font-size: 12px; }
.chevron { flex: none; color: var(--kg-text-tertiary); transition: transform var(--kg-motion-base); }
.chevron.open { transform: rotate(90deg); }

.snapshot-detail {
  max-height: 330px;
  margin: 3px 0 12px 42px;
  padding: 11px 12px;
  overflow: auto;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-code);
}

.snapshot-item + .snapshot-item { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--kg-border-subtle); }
.snapshot-key { margin-bottom: 4px; color: var(--kg-text-tertiary); font-size: 12px; }
.snapshot-detail pre,
.intent-detail { margin: 0; color: var(--kg-text-secondary); font: 12px/1.55 var(--kg-font-mono); white-space: pre-wrap; word-break: break-all; }

.assistant {
  margin: 16px 0 20px 42px;
  color: var(--kg-text-primary);
  font-size: 14px;
}

.assistant-state {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 8px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.state-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--kg-info); }

.assistant.thinking {
  padding: 10px 12px;
  border-left: 2px solid var(--kg-info-border);
  color: var(--kg-text-secondary);
}

.assistant.aborted {
  padding: 10px 12px;
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-danger-soft);
}

.cursor { color: var(--kg-accent); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.phase-bar {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 12px 42px;
  padding: 8px 11px;
  border: 1px solid var(--kg-info-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-info-soft);
}

.phase-text { flex: 1; color: var(--kg-text-secondary); font-size: 13px; }
.phase-timer { color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); }

.intent-card {
  margin: 12px 0 12px 42px;
  padding: 12px;
  border: 1px solid var(--kg-danger-border);
  border-left: 3px solid var(--kg-danger);
  border-radius: var(--kg-radius-md);
  background: var(--kg-danger-soft);
  cursor: pointer;
}

.intent-head { display: flex; align-items: center; gap: 9px; }
.intent-icon { color: var(--kg-danger); }
.intent-head strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 600; }
.intent-head code { max-width: 230px; margin-left: auto; overflow: hidden; color: var(--kg-text-tertiary); font: 11px/1.4 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.intent-reason { margin: 8px 0 0 25px; color: var(--kg-text-secondary); font-size: 13px; }
.intent-detail { margin: 10px 0 0 25px; padding: 10px; border-radius: var(--kg-radius-sm); background: var(--kg-bg-code); }
.fatal { margin: 12px 0 12px 42px; }

.composer {
  flex: none;
  padding: 8px 24px 14px;
  background: var(--kg-bg-canvas);
}

.composer-shell {
  width: min(100%, var(--kg-thread-max));
  margin: 0 auto;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: inset 0 1px rgb(255 255 255 / 2.5%);
  transition: border-color var(--kg-motion-fast), box-shadow var(--kg-motion-fast);
}

.composer-shell:focus-within {
  border-color: var(--kg-accent);
  box-shadow: 0 0 0 3px rgb(107 194 177 / 12%), inset 0 1px rgb(255 255 255 / 2.5%);
}

.composer-box { position: relative; padding: 11px 52px 8px 13px; }
.composer-box :deep(.el-textarea__inner) { max-height: 180px; padding: 0; border: 0; background: transparent; box-shadow: none; color: var(--kg-text-primary); font: 14px/1.6 var(--kg-font-ui); }

.send-btn {
  position: absolute;
  right: 9px;
  bottom: 8px;
  width: 34px;
  height: 34px;
  display: grid;
  padding: 0;
  place-items: center;
  border: 1px solid var(--kg-accent);
  border-radius: var(--kg-radius-md);
  background: var(--kg-accent);
  color: var(--kg-text-on-accent);
  cursor: pointer;
}

.send-btn:hover:not(:disabled) { background: var(--kg-accent-hover); }
.send-btn:disabled { border-color: var(--kg-border-subtle); background: var(--kg-bg-surface-2); color: var(--kg-text-disabled); cursor: not-allowed; }
.send-spinner { width: 13px; height: 13px; border: 2px solid var(--kg-border-strong); border-top-color: var(--kg-text-primary); border-radius: 50%; animation: kg-spin 800ms linear infinite; }

.composer-footer {
  min-height: 30px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-3);
  padding: 0 9px 7px 13px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.inline-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  cursor: pointer;
}

.inline-action:hover:not(:disabled) { border-color: var(--kg-border-default); background: var(--kg-bg-surface-2); color: var(--kg-text-primary); }
.inline-action:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }

@media (max-width: 1080px) {
  .chat-inner { padding: 22px 20px 32px; }
  .composer { padding-right: 20px; padding-left: 20px; }
  .record-meta { display: none; }
}
</style>
