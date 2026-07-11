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
                     class="assistant" :class="[it.role, { aborted: it.aborted, interrupted: it.interrupted }]">
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

            <TaskError v-else-if="it.kind === 'task_error'" :item="it"
                       :disabled="running" @retry="retryTask(it)" />

            <el-alert v-else-if="it.kind === 'fatal'" type="error"
                      :closable="false" :title="it.error" class="fatal" />
          </template>

          <div v-if="showTurnActivity" class="turn-activity"
               :class="`is-${currentTurn.status}`" aria-live="polite">
            <span class="activity-node">
              <span v-if="running" class="kg-spinner" aria-hidden="true"></span>
              <KgIcon v-else name="close" :size="13" />
            </span>
            <span class="activity-copy">
              <strong>{{ activityText }}</strong>
              <small v-if="activityMeta">{{ activityMeta }}</small>
            </span>
            <span class="activity-timer">{{ elapsedText }}</span>
            <button v-if="running" type="button" class="stop-action" @click="stopTurn">
              <span class="stop-square" aria-hidden="true"></span>停止
            </button>
          </div>
        </div>
      </div>

      <div class="composer">
        <div class="composer-shell">
          <div class="composer-box">
            <el-input v-model="input" type="textarea" :rows="1" autosize
                      resize="none" placeholder="描述运维任务…"
                      @keydown.enter.exact.prevent="submit" @keydown.esc="stopTurn" />
            <button type="button" class="send-btn" :class="{ stop: running }"
                    :aria-label="running ? '停止等待' : '发送运维指令'"
                    :disabled="!running && !input.trim()"
                    @click="running ? stopTurn() : submit()">
              <span v-if="running" class="stop-square"></span>
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
import TaskError from '../components/TaskError.vue'
import TraceStep from '../components/TraceStep.vue'
import {
  activeId, cancelCurrentTurn, currentTurn, generateReport, items,
  retryMessage, running, sendMessage,
} from '../composables/useChat.js'

const WELCOME_HINTS = [
  { icon: 'disk', title: '检查磁盘使用', description: '只读取状态', text: '查看磁盘使用情况' },
  { icon: 'cpu', title: '查看高占用进程', description: '只读取状态', text: '列出 CPU 占用最高的进程' },
  { icon: 'activity', title: '检查失败服务', description: '只读取状态', text: '检查失败的服务' },
]

const input = ref('')
const chatRef = ref(null)

const ageText = (age) => (age < 3 ? '刚刚' : `${Math.round(age)} 秒前`)

const now = ref(Date.now())
let timerId = null
watch(running, (active) => {
  if (active) {
    now.value = Date.now()
    if (timerId) clearInterval(timerId)
    timerId = setInterval(() => {
      now.value = Date.now()
    }, 100)
  } else if (timerId) {
    clearInterval(timerId)
    timerId = null
  }
})
onUnmounted(() => timerId && clearInterval(timerId))

const currentActivity = computed(() => currentTurn.value?.activities?.at(-1) || null)
const showTurnActivity = computed(() => currentTurn.value
  && ['running', 'retry_wait', 'waiting_user', 'cancelling', 'cancelled'].includes(currentTurn.value.status))

const activityText = computed(() => {
  const turn = currentTurn.value
  const activity = currentActivity.value
  if (!turn) return ''
  if (turn.status === 'cancelling') return '正在停止等待…'
  if (turn.status === 'cancelled') return '已停止等待后续结果'
  if (turn.status === 'waiting_user') return '等待你的确认'
  const stage = activity?.stage || turn.stage
  const state = activity?.state
  if (state === 'retry_wait') {
    return stage === 'reviewing' ? '安全检查暂时没有响应' : '模型服务暂时没有响应'
  }
  if (state === 'failed') {
    return stage === 'executing' ? '操作执行失败' : '本次请求未能完成'
  }
  if (state === 'completed') {
    if (stage === 'planning') return '分析完成，正在整理下一步…'
    if (stage === 'reviewing') return '安全检查已完成…'
    if (stage === 'executing') return '操作已完成，正在整理结果…'
  }
  if (stage === 'planning') return state === 'streaming' ? '正在分析请求…' : '正在连接规划模型…'
  if (stage === 'reviewing') return '正在检查操作是否安全…'
  if (stage === 'executing') return '正在执行操作…'
  if (stage === 'confirmation') return '等待你的确认'
  return '正在准备…'
})

const activityMeta = computed(() => {
  const turn = currentTurn.value
  const activity = currentActivity.value
  if (!turn) return ''
  if (turn.status === 'cancelled') return '已停止接收后续结果；已经开始的操作不会自动回滚'
  if (turn.status === 'waiting_user' && activity?.deadlineAt) {
    const seconds = Math.max(0, Math.ceil((activity.deadlineAt - now.value) / 1000))
    const minutes = Math.floor(seconds / 60)
    const tail = String(seconds % 60).padStart(2, '0')
    return `${minutes}:${tail} 后自动拒绝`
  }
  if (activity?.state === 'retry_wait') {
    const remaining = Math.max(0, Math.ceil((activity.retryInMs - (now.value - activity.updatedAt)) / 1000))
    const attempt = activity.attempt && activity.maxAttempts
      ? ` · 已尝试 ${activity.attempt}/${activity.maxAttempts} 次` : ''
    return `${remaining} 秒后重试${attempt}`
  }
  if (activity?.attempt && activity.maxAttempts > 1) {
    return `第 ${activity.attempt}/${activity.maxAttempts} 次尝试`
  }
  return ''
})

const elapsedText = computed(() => {
  const turn = currentTurn.value
  if (!turn) return ''
  const elapsed = turn.elapsedMs ?? Math.max(0, (turn.endedAt || now.value) - turn.startedAt)
  if (elapsed >= 60000) return `${Math.floor(elapsed / 60000)}:${String(Math.floor(elapsed / 1000) % 60).padStart(2, '0')}`
  return `${(elapsed / 1000).toFixed(1)}s`
})

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
watch(() => `${currentTurn.value?.status}:${currentActivity.value?.id}:${currentActivity.value?.state}`,
  scrollToBottom)

async function submit() {
  if (running.value) return
  const text = input.value.trim()
  if (!text) return
  input.value = ''
  await sendMessage(text, { onUpdate: scrollToBottom })
}

function stopTurn() {
  cancelCurrentTurn()
}

async function retryTask(item) {
  await retryMessage(item.prompt, { onUpdate: scrollToBottom })
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
.assistant.interrupted { border-left: 2px solid var(--kg-danger-border); padding-left: 12px; }

.cursor { color: var(--kg-accent); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.turn-activity {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 12px 42px;
  padding: 8px 11px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}
.activity-node { width: 18px; display: grid; place-items: center; flex: none; color: var(--kg-info); }
.turn-activity.is-cancelled .activity-node { color: var(--kg-text-tertiary); }
.activity-copy { min-width: 0; display: flex; flex: 1; align-items: baseline; gap: 8px; }
.activity-copy strong { color: var(--kg-text-secondary); font-size: 13px; font-weight: 500; }
.activity-copy small { overflow: hidden; color: var(--kg-text-tertiary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.activity-timer { flex: none; color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); }
.stop-action { display: inline-flex; align-items: center; gap: 6px; padding: 4px 7px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); font-size: 12px; cursor: pointer; }
.stop-action:hover { border-color: var(--kg-border-default); color: var(--kg-text-primary); }
.stop-square { width: 9px; height: 9px; display: block; border-radius: 2px; background: currentColor; }

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
.send-btn.stop { border-color: var(--kg-border-default); background: var(--kg-bg-surface-2); color: var(--kg-text-secondary); }
.send-btn.stop:hover { border-color: var(--kg-danger-border); color: var(--kg-danger); }
.send-btn:disabled { border-color: var(--kg-border-subtle); background: var(--kg-bg-surface-2); color: var(--kg-text-disabled); cursor: not-allowed; }

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
