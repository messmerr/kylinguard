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
              <button v-for="(hint, index) in WELCOME_HINTS" :key="hint.text" type="button"
                      class="hint kg-enter" :style="{ '--kg-enter-delay': `${index * 80}ms` }"
                      :disabled="composerDisabled" @click="sendHint(hint.text)">
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
              <div class="user-content">
                <div class="user-text">
                  <template v-for="(node, nodeIndex) in historyNodes(it)" :key="nodeIndex">
                    <span v-if="node.type === 'text'">{{ node.text }}</span>
                    <span
                      v-else
                      class="user-inline-context"
                      :class="node.type"
                      :title="node.type === 'file' ? node.relativePath : node.id"
                    >
                      <KgIcon :name="node.type === 'skill' ? 'skill' : 'log'" :size="11" />
                      @{{ node.label }}
                    </span>
                  </template>
                </div>
                <div v-if="showHistoryMetadata(it)" class="user-context">
                  <span v-if="showHistorySkillMetadata(it)" class="user-skill" :class="`is-${it.skillMode}`">
                    <KgIcon name="skill" :size="11" />{{ historySkillLabel(it) }}
                  </span>
                  <span v-for="file in legacyHistoryFiles(it)" :key="file.path" class="user-file" :title="file.path">
                    <KgIcon name="log" :size="11" />{{ file.relativePath || file.name }}
                  </span>
                </div>
              </div>
            </article>

            <section v-else-if="it.kind === 'snapshot'" class="snapshot-block">
              <button type="button" class="record-line" :aria-expanded="it.expanded"
                      @click="it.expanded = !it.expanded">
                <span class="record-node"><KgIcon name="activity" :size="14" /></span>
                <span class="record-title">已读取系统状态</span>
                <span class="record-meta">{{ ageText(it) }}采集</span>
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
                <span>{{ it.streaming ? '模型思考中' : '思考过程' }}</span>
              </div>
              <RichMessage :text="it.text" />
              <span v-if="it.streaming" class="cursor">▍</span>
              <div v-if="it.role === 'answer' && !it.streaming && it.model?.modelId"
                   class="assistant-model" :title="it.model.providerName || it.model.providerId">
                <KgIcon name="model" :size="12" />
                <span>{{ it.model.modelLabel || it.model.modelId }}</span>
                <span>· 推理{{ effortText(it.model.reasoningEffort) }}</span>
              </div>
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
                       :disabled="composerDisabled" @retry="retryTask(it)"
                       @configure-model="emit('open-model-settings')" />

            <el-alert v-else-if="it.kind === 'fatal'" type="error"
                      :closable="false" :title="it.error" class="fatal" />

            <el-alert v-else-if="it.kind === 'history_warning'" type="warning"
                      :closable="false" :title="it.error" class="fatal" />
          </template>

          <div v-if="showTurnActivity" class="turn-activity"
               :class="`is-${currentTurn.status}`">
            <span class="activity-node" :class="`is-${activityIndicator}`">
              <span v-if="activityIndicator === 'thinking'" class="thinking-dots" aria-hidden="true">
                <i></i><i></i><i></i>
              </span>
              <KgIcon v-else-if="activityIndicator === 'confirmation'" name="lock" :size="13" />
              <KgIcon v-else-if="activityIndicator === 'retry'" name="refresh" :size="13" />
              <KgIcon v-else-if="activityIndicator === 'stopped'" name="close" :size="13" />
              <span v-else-if="running" class="kg-spinner" aria-hidden="true"></span>
              <KgIcon v-else name="close" :size="13" />
            </span>
            <span class="activity-copy" aria-live="polite" aria-atomic="true">
              <strong>{{ activityText }}</strong>
              <small v-if="activityMeta">{{ activityMeta }}</small>
            </span>
            <span class="activity-timer" aria-hidden="true"
                  :title="`本轮已用时 ${elapsedText}`">{{ elapsedText }}</span>
            <button v-if="running" type="button" class="stop-action" @click="stopTurn">
              <span class="stop-square" aria-hidden="true"></span>停止
            </button>
          </div>
        </div>
      </div>

      <div class="composer">
        <div class="composer-shell">
          <div v-if="mentionOpen" id="composer-mention-menu" ref="mentionMenuRef"
               class="mention-menu" role="listbox" aria-label="@ 上下文候选">
            <div class="mention-head">
              <strong>添加到本轮任务</strong>
              <span><kbd>↑↓</kbd> 选择　<kbd>Enter</kbd>/<kbd>Tab</kbd> 确认　<kbd>Esc</kbd> 关闭</span>
            </div>
            <section v-if="skillCandidates.length" class="mention-group">
              <h3>Skills</h3>
              <button
                v-for="(candidate, index) in skillCandidates"
                :key="candidate.key"
                :id="`composer-mention-option-${index}`"
                type="button"
                role="option"
                :data-candidate-index="index"
                :aria-selected="activeIndex === index"
                :aria-disabled="candidate.disabled"
                :disabled="candidate.disabled"
                :class="{ active: activeIndex === index }"
                @mouseenter="activeIndex = index"
                @mousedown.prevent="chooseMention(candidate)"
              >
                <span class="mention-icon"><KgIcon name="skill" :size="14" /></span>
                <span class="mention-copy"><strong>{{ candidate.title }}</strong><small>{{ candidate.detail }}</small></span>
              </button>
              <div v-if="skillsLimitReached" class="mention-status">本轮最多指定 4 个 Skill</div>
            </section>
            <section class="mention-group">
              <h3>服务器文件</h3>
              <button
                v-for="(candidate, index) in fileCandidates"
                :key="candidate.key"
                :id="`composer-mention-option-${skillCandidates.length + index}`"
                type="button"
                role="option"
                :data-candidate-index="skillCandidates.length + index"
                :aria-selected="activeIndex === skillCandidates.length + index"
                :aria-disabled="candidate.disabled"
                :disabled="candidate.disabled"
                :class="{ active: activeIndex === skillCandidates.length + index }"
                @mouseenter="activeIndex = skillCandidates.length + index"
                @mousedown.prevent="chooseMention(candidate)"
              >
                <span class="mention-icon file"><KgIcon name="log" :size="14" /></span>
                <span class="mention-copy"><strong>{{ candidate.title }}</strong><small>{{ candidate.detail }}</small></span>
              </button>
              <div v-if="filesLoading" class="mention-status"><span class="kg-spinner"></span>正在检索服务器工作目录…</div>
              <div v-else-if="filesLimitReached" class="mention-status">本轮最多引用 8 个服务器文件</div>
              <div v-else-if="filesError" class="mention-status error">服务器文件暂不可用：{{ filesError }}</div>
              <div v-else-if="!fileCandidates.length" class="mention-status">没有匹配的服务器文件</div>
            </section>
            <div v-if="!allCandidates.length && !filesLoading" class="mention-empty">没有匹配“{{ mentionQuery }}”的候选项</div>
          </div>
          <div class="composer-box">
            <InlineMentionEditor
              ref="composerEditorRef"
              v-model="editorNodes"
              :disabled="composerDisabled"
              placeholder="描述运维任务…"
              aria-label="描述运维任务"
              menu-id="composer-mention-menu"
              :menu-open="mentionOpen"
              :active-descendant="mentionOpen && allCandidates.length && activeIndex >= 0
                ? `composer-mention-option-${activeIndex}` : ''"
              @query-change="handleMentionQuery"
              @mention-keydown="onMentionKeydown"
              @submit="submit"
              @escape="stopTurn"
            />
          </div>
          <div class="composer-footer">
            <div class="composer-meta">
              <button
                type="button"
                class="workspace-control"
                :class="{ locked: Boolean(activeId) }"
                :disabled="composerDisabled || Boolean(activeId)"
                :title="activeId
                  ? `服务器工作目录已锁定：${workspaceDisplay}`
                  : `设置服务器工作目录：${workspaceDisplay}`"
                :aria-label="activeId
                  ? `服务器工作目录已锁定：${workspaceDisplay}`
                  : `设置服务器工作目录，当前为 ${workspaceDisplay}`"
                @click="chooseWorkspaceRoot"
              >
                <KgIcon name="disk" :size="13" />
                <span>{{ workspaceDisplay }}</span>
                <KgIcon :name="activeId ? 'lock' : 'chevron'" :size="11" class="workspace-state" />
              </button>
              <span class="composer-separator" aria-hidden="true"></span>
              <button
                type="button"
                class="context-trigger"
                :disabled="composerDisabled"
                title="输入 @ 可指定 Skill 或引用服务器工作目录中的文件"
                @click="insertMentionTrigger"
              >
                <span class="at-mark">@</span>
                <span>添加上下文</span>
              </button>
              <span class="composer-separator" aria-hidden="true"></span>
              <ModelSelector
                :disabled="composerDisabled"
                :has-history="Boolean(items.length)"
                @configure="emit('open-model-settings')"
              />
              <span class="composer-separator" aria-hidden="true"></span>
              <PermissionSelector
                :disabled="sessionLoading || (running && currentTurn?.status !== 'waiting_user')"
              />
            </div>
            <div class="composer-actions">
              <button v-if="activeId && items.length" type="button" class="inline-action"
                       :disabled="composerDisabled" @click="genReport">
                <KgIcon name="task" :size="13" />生成运维报告
              </button>
              <button type="button" class="send-btn" :class="{ stop: running }"
                      :aria-label="sessionLoading ? '正在加载任务' : running ? '停止本轮处理' : '发送运维指令'"
                      :disabled="sessionLoading || (!running && !canSubmit)"
                      @click="running ? stopTurn() : submit()">
                <span v-if="running" class="stop-square"></span>
                <span v-else-if="sessionLoading" class="kg-spinner" aria-hidden="true"></span>
                <KgIcon v-else name="arrowUp" :size="16" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ConfirmCard from '../components/ConfirmCard.vue'
import InlineMentionEditor from '../components/InlineMentionEditor.vue'
import KgIcon from '../components/KgIcon.vue'
import KgLogo from '../components/KgLogo.vue'
import RichMessage from '../components/RichMessage.vue'
import ModelSelector from '../components/ModelSelector.vue'
import PermissionSelector from '../components/PermissionSelector.vue'
import TaskError from '../components/TaskError.vue'
import TraceStep from '../components/TraceStep.vue'
import {
  activeChatContextKey, activeId, cancelCurrentTurn, chatDraftNodes, currentTurn,
  generateReport, items, retryMessage, running, sendMessage, sessionLoading,
  setChatDraft,
} from '../composables/useChat.js'
import {
  permissionContext,
  setDraftWorkspaceRoot,
} from '../composables/usePermissions.js'
import { useComposerMentions } from '../composables/useComposerMentions.js'
import { effortLabel } from '../composables/useModels.js'
import {
  planningProgressCount,
  turnProgressText,
} from '../utils/progressPresentation.js'
import { formatCollectionAge } from '../utils/relativeTime.js'
import { editorPlainText } from '../utils/contextMention.js'

const WELCOME_HINTS = [
  { icon: 'disk', title: '检查磁盘使用', description: '只读取状态', text: '查看磁盘使用情况' },
  { icon: 'cpu', title: '查看高占用进程', description: '只读取状态', text: '列出 CPU 占用最高的进程' },
  { icon: 'activity', title: '检查失败服务', description: '只读取状态', text: '检查失败的服务' },
]

const emit = defineEmits(['open-model-settings', 'open-extensions'])

const editorNodes = chatDraftNodes
const composerEditorRef = ref(null)
const mentionMenuRef = ref(null)
const chatRef = ref(null)
const canSubmit = computed(() => Boolean(editorPlainText(editorNodes.value).trim()))
const composerDisabled = computed(() => running.value || sessionLoading.value)
const workspaceDisplay = computed(() => (
  permissionContext.workspaceRoot || '服务器默认目录'
))
const mentionWorkspaceRoot = computed(() => permissionContext.workspaceRoot || '')
const {
  activeIndex,
  allCandidates,
  chooseCandidate,
  fileCandidates,
  filesError,
  filesLimitReached,
  filesLoading,
  handleMentionKeydown,
  handleQueryChange: handleMentionQuery,
  insertTrigger: insertMentionTrigger,
  mentionOpen,
  mentionQuery,
  skillCandidates,
  skillsLimitReached,
  takeDraftContext,
} = useComposerMentions({
  nodes: editorNodes,
  editorRef: composerEditorRef,
  workspaceRoot: mentionWorkspaceRoot,
  disabled: composerDisabled,
})

const snapshotClock = ref(Date.now())
let snapshotClockTimer = null
const ageText = (item) => formatCollectionAge(
  (snapshotClock.value - (item.collectedAt || snapshotClock.value)) / 1000,
)

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
onMounted(() => {
  snapshotClockTimer = setInterval(() => { snapshotClock.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (timerId) clearInterval(timerId)
  clearInterval(snapshotClockTimer)
})

const currentActivity = computed(() => currentTurn.value?.activities?.at(-1) || null)
const showTurnActivity = computed(() => currentTurn.value
  && ['running', 'retry_wait', 'waiting_user', 'cancelling', 'cancelled'].includes(currentTurn.value.status))

const activityIndicator = computed(() => {
  const turn = currentTurn.value
  const activity = currentActivity.value
  if (!turn) return 'idle'
  if (turn.status === 'waiting_user') return 'confirmation'
  if (turn.status === 'retry_wait') return 'retry'
  if (turn.status === 'cancelled') return 'stopped'
  if (turn.status === 'cancelling') return 'busy'
  const stage = activity?.stage || turn.stage
  const state = activity?.state
  if (running.value && ['planning', 'reviewing'].includes(stage)
      && !['completed', 'failed', 'retry_wait'].includes(state)) {
    return 'thinking'
  }
  return running.value ? 'busy' : 'idle'
}, { immediate: true })

const activityText = computed(() => {
  return turnProgressText(currentTurn.value, currentActivity.value)
})

function retryReasonText(value) {
  return String(value || '')
    .replace(/[，,]?\s*请稍后重试[。.!！]?$/u, '')
    .replace(/[。.!！]+$/u, '')
    .trim()
}

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
    const nextAttempt = activity.attempt && activity.maxAttempts
      ? Math.min(activity.attempt + 1, activity.maxAttempts) : null
    const attempt = nextAttempt
      ? ` · 下一次尝试 ${nextAttempt}/${activity.maxAttempts}` : ''
    const reason = retryReasonText(activity.error?.message)
    return `${remaining} 秒后自动重试${attempt}${reason ? ` · ${reason}` : ''}`
  }
  const detail = []
  const generated = planningProgressCount(activity)
  if (generated) detail.push(generated)
  if (activity?.attempt > 1 && activity.maxAttempts > 1) {
    detail.push(`第 ${activity.attempt}/${activity.maxAttempts} 次尝试`)
  }
  return detail.join(' · ')
})

const elapsedText = computed(() => {
  const turn = currentTurn.value
  if (!turn) return ''
  const elapsed = turn.elapsedMs ?? Math.max(0, (turn.endedAt || now.value) - turn.startedAt)
  if (elapsed >= 60000) return `${Math.floor(elapsed / 60000)}:${String(Math.floor(elapsed / 1000) % 60).padStart(2, '0')}`
  return `${(elapsed / 1000).toFixed(1)}s`
})

const effortText = (effort) => effortLabel(effort)

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
watch(() => [
  currentTurn.value?.status,
  currentActivity.value?.id,
  currentActivity.value?.state,
  currentActivity.value?.planningActivity,
  currentActivity.value?.generatedChars,
  currentActivity.value?.generatedBytes,
].join(':'),
  scrollToBottom)
watch(activeIndex, (index) => {
  nextTick(() => mentionMenuRef.value
    ?.querySelector(`[data-candidate-index="${index}"]`)
    ?.scrollIntoView({ block: 'nearest' }))
})

function chooseMention(candidate) {
  chooseCandidate(candidate)
}

function onMentionKeydown(payload) {
  handleMentionKeydown(payload)
}

function showBusyMessage(result) {
  if (!['workspace_busy', 'session_busy'].includes(result?.reason)) return
  if (result.contextKey !== activeChatContextKey.value) return
  ElMessage.warning(result.message || '该工作目录正在被其他任务使用，当前仅可查看')
}

async function submit() {
  if (composerDisabled.value || !canSubmit.value) return
  const snapshot = takeDraftContext()
  const result = await sendMessage(snapshot.message, { onUpdate: scrollToBottom, ...snapshot })
  if (result?.reason === 'workspace_busy' || result?.reason === 'session_busy') {
    setChatDraft(snapshot.contentNodes, result.contextKey)
  }
  showBusyMessage(result)
}

async function chooseWorkspaceRoot() {
  if (activeId.value || running.value) return
  try {
    const { value } = await ElMessageBox.prompt(
      '输入 Agent 所在服务器上的绝对目录。它不是浏览器本地文件夹，也不是安全沙箱；工作目录只决定命令默认从哪里开始。',
      '设置服务器工作目录',
      {
        inputValue: permissionContext.workspaceRoot,
        inputPlaceholder: '例如 /srv/project',
        confirmButtonText: '使用此目录',
        cancelButtonText: '取消',
        inputValidator: (value) => (
          String(value || '').trim().startsWith('/')
          || '请输入以 / 开头的服务器绝对路径'
        ),
      },
    )
    setDraftWorkspaceRoot(String(value || '').trim())
  } catch (error) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel') return
    throw error
  }
}

function stopTurn() {
  cancelCurrentTurn()
}

async function retryTask(item) {
  const result = await retryMessage(item.prompt, {
    onUpdate: scrollToBottom,
    skillId: item.skillId || '',
    skillIds: item.skillIds || [],
    skillMode: item.skillMode || 'auto',
    contextFiles: item.contextFiles || [],
    contextMentions: item.contextMentions || [],
    contentNodes: item.contentNodes || [],
  })
  showBusyMessage(result)
}

async function sendHint(text) {
  if (running.value) return
  const result = await sendMessage(text, { onUpdate: scrollToBottom })
  showBusyMessage(result)
}

async function genReport() {
  const result = await generateReport({ onUpdate: scrollToBottom })
  showBusyMessage(result)
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

function historySkillLabel(item) {
  if (item.skillMode === 'none') return 'Skill · 未使用'
  if (item.skillMode === 'manual') {
    const names = item.skillNames?.filter(Boolean).join('、')
      || item.skillIds?.join('、') || item.skillName || item.skillId
    return `手动 · ${names || '指定 Skill'}`
  }
  const routedId = item.routedSkillId || item.skillId
  const routedName = item.routedSkillName || item.skillName
  return routedId
    ? `自动 · ${routedName || routedId}`
    : item.skillResolved ? '自动 · 未使用 Skill' : 'Skill · 自动匹配'
}

function historyNodes(item) {
  return Array.isArray(item.contentNodes) && item.contentNodes.length
    ? item.contentNodes : [{ type: 'text', text: item.text || '' }]
}

function showHistorySkillMetadata(item) {
  if (item.skillMode === 'auto' || item.skillMode === 'none') return true
  return item.skillMode === 'manual'
    && !item.contextMentions?.length
    && Boolean(item.skillId || item.skillIds?.length)
}

function showHistoryMetadata(item) {
  return showHistorySkillMetadata(item) || Boolean(legacyHistoryFiles(item).length)
}

function legacyHistoryFiles(item) {
  const inlinePaths = new Set((item.contextMentions || [])
    .filter((mention) => mention.type === 'file')
    .map((mention) => mention.relativePath))
  return (item.contextFiles || []).filter((file) => !inlinePaths.has(file.relativePath))
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
  background: var(--kg-bg-canvas);
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
  box-shadow: 0 8px 24px rgb(23 92 255 / 10%);
  /* 柔和的呼吸光晕，给空态一点生机 */
  animation: kg-pulse-ring 3.2s var(--kg-ease-standard) infinite;
}

.welcome h1 {
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 22px;
  font-weight: 650;
  line-height: 30px;
  letter-spacing: .01em;
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
    border-color var(--kg-motion-fast), box-shadow var(--kg-motion-fast),
    transform var(--kg-motion-base) var(--kg-ease-spring);
  box-shadow: var(--kg-shadow-sm);
}

.hint:hover:not(:disabled) {
  border-color: rgb(23 92 255 / 32%);
  background: var(--kg-accent-soft);
  color: var(--kg-accent);
  box-shadow: var(--kg-shadow-md);
  transform: translateY(-2px);
}

.hint:hover:not(:disabled) .hint-icon {
  background: var(--kg-accent);
  color: #fff;
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
  transition: background var(--kg-motion-fast), color var(--kg-motion-fast);
}

.hint-copy { min-width: 0; flex: 1; display: grid; gap: 2px; }
.hint-copy strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 500; }
.hint-copy small { color: var(--kg-text-tertiary); font-size: 12px; }

.user-prompt {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 0;
  margin: 16px 0 18px;
  padding-left: 2px;
}

.user-chevron { display: none; }
.user-content { max-width: min(78%, 680px); display: grid; justify-items: end; gap: 6px; }

.user-text {
  max-width: 100%;
  padding: 10px 14px;
  border: 1px solid #c9d9ff;
  border-radius: var(--kg-radius-lg) var(--kg-radius-lg) var(--kg-radius-xs) var(--kg-radius-lg);
  background: var(--kg-accent-soft);
  color: #18315f;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.6;
  white-space: pre-wrap;
}
.user-inline-context {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin: 0 3px;
  padding: 1px 6px;
  border: 1px solid #bfd1ff;
  border-radius: var(--kg-radius-sm);
  background: #fff;
  color: var(--kg-accent);
  font-size: 12px;
  font-weight: 550;
  line-height: 20px;
  vertical-align: baseline;
}
.user-inline-context.file { border-color: var(--kg-border-default); color: var(--kg-text-secondary); }
.user-context { max-width: 100%; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px; }
.user-skill, .user-file { max-width: 260px; display: inline-flex; align-items: center; gap: 4px; overflow: hidden; color: var(--kg-text-tertiary); font: 10px/1.4 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.user-skill.is-manual { color: var(--kg-accent); }

.snapshot-block { margin: 14px 0; }

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
  box-shadow: 0 8px 22px rgb(17 24 39 / 10%);
}

.snapshot-item + .snapshot-item { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--kg-border-subtle); }
.snapshot-key { margin-bottom: 4px; color: #93a4be; font-size: 12px; }
.snapshot-detail pre,
.intent-detail { margin: 0; color: #dbe5f4; font: 12px/1.55 var(--kg-font-mono); white-space: pre-wrap; word-break: break-all; }

.assistant {
  margin: 18px 0 16px 42px;
  color: var(--kg-text-primary);
  font-size: 14px;
}

/* TraceStep 根节点在子组件内，这里统一加大条目上下间距，与消息节奏对齐 */
.chat-inner :deep(.step-block) { margin: 14px 0; }

.assistant.answer,
.assistant.streaming {
  padding: 15px 17px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: #fff;
  box-shadow: 0 4px 16px rgb(34 52 84 / 5%);
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
.assistant-model { display: flex; align-items: center; gap: 5px; margin-top: 8px; color: var(--kg-text-tertiary); font-size: 11px; }
.assistant-model span:first-of-type { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.cursor { color: var(--kg-accent); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.turn-activity {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0 14px 42px;
  padding: 8px 13px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-sm);
}
.activity-node { width: 18px; display: grid; place-items: center; flex: none; color: var(--kg-info); }
.activity-node.is-confirmation { color: var(--kg-warning); }
.activity-node.is-retry { color: var(--kg-warning); }
.turn-activity.is-cancelled .activity-node { color: var(--kg-text-tertiary); }
.thinking-dots { display: inline-flex; align-items: center; gap: 2px; height: 12px; }
.thinking-dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
  animation: thinking-dot 1.15s ease-in-out infinite;
}
.thinking-dots i:nth-child(2) { animation-delay: 140ms; }
.thinking-dots i:nth-child(3) { animation-delay: 280ms; }
@keyframes thinking-dot {
  0%, 70%, 100% { opacity: .35; transform: translateY(0); }
  35% { opacity: 1; transform: translateY(-2px); }
}
.activity-copy { min-width: 0; display: flex; flex: 1; align-items: baseline; gap: 8px; }
.activity-copy strong { color: var(--kg-text-secondary); font-size: 13px; font-weight: 500; }
.activity-copy small { overflow: hidden; color: var(--kg-text-tertiary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.activity-timer { flex: none; color: var(--kg-text-tertiary); font: 12px/1 var(--kg-font-mono); }
.stop-action { display: inline-flex; align-items: center; gap: 6px; padding: 4px 7px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); font-size: 12px; cursor: pointer; }
.stop-action:hover { border-color: var(--kg-border-default); color: var(--kg-text-primary); }
.stop-square { width: 9px; height: 9px; display: block; border-radius: 2px; background: currentColor; }

@media (prefers-reduced-motion: reduce) {
  .thinking-dots i { animation: none; opacity: .75; transform: none; }
}

.intent-card {
  margin: 16px 0 16px 42px;
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
.fatal { margin: 16px 0 16px 42px; }
.composer {
  flex: none;
  padding: 8px 24px 14px;
  background: linear-gradient(to bottom, rgb(248 250 255 / 0%), #f8faff 34%);
}

.composer-shell {
  position: relative;
  width: min(100%, var(--kg-thread-max));
  margin: 0 auto;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-xl);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-md);
  transition: border-color var(--kg-motion-fast), box-shadow var(--kg-motion-fast);
}

.composer-shell:focus-within {
  border-color: var(--kg-accent);
  box-shadow: 0 0 0 3px rgb(23 92 255 / 10%), var(--kg-shadow-md);
}

.mention-menu {
  position: absolute;
  z-index: 20;
  bottom: calc(100% + 8px);
  left: 0;
  width: min(540px, 100%);
  max-height: min(430px, 58vh);
  padding: 6px;
  overflow-y: auto;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-lg);
}
.mention-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 8px 8px; border-bottom: 1px solid var(--kg-border-subtle); }
.mention-head strong { color: var(--kg-text-primary); font-size: 12px; font-weight: 600; }
.mention-head span { color: var(--kg-text-tertiary); font-size: 10px; white-space: nowrap; }
.mention-head kbd { padding: 1px 4px; border: 1px solid var(--kg-border-subtle); border-radius: 3px; background: var(--kg-bg-surface-2); font: 9px/1.4 var(--kg-font-mono); }
.mention-group { padding-top: 5px; }
.mention-group + .mention-group { margin-top: 3px; border-top: 1px solid var(--kg-border-subtle); }
.mention-group h3 { margin: 0; padding: 4px 8px; color: var(--kg-text-tertiary); font-size: 10px; font-weight: 550; letter-spacing: .04em; text-transform: uppercase; }
.mention-group > button { width: 100%; min-height: 43px; display: flex; align-items: center; gap: 9px; padding: 6px 8px; border: 0; border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); text-align: left; cursor: pointer; }
.mention-group > button.active, .mention-group > button:hover { background: var(--kg-selection); color: var(--kg-accent); }
.mention-group > button:disabled { background: transparent; color: var(--kg-text-disabled); cursor: default; opacity: .62; }
.mention-icon { width: 28px; height: 28px; display: grid; flex: none; place-items: center; border-radius: var(--kg-radius-sm); background: var(--kg-accent-soft); color: var(--kg-accent); }
.mention-icon.file { background: var(--kg-bg-surface-3); color: var(--kg-text-secondary); }
.mention-copy { min-width: 0; display: grid; flex: 1; gap: 2px; }
.mention-copy strong, .mention-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mention-copy strong { color: var(--kg-text-primary); font-size: 12px; font-weight: 550; }
.mention-copy small { color: var(--kg-text-tertiary); font: 10px/1.4 var(--kg-font-mono); }
.mention-status, .mention-empty { min-height: 34px; display: flex; align-items: center; gap: 7px; padding: 6px 9px; color: var(--kg-text-tertiary); font-size: 11px; }
.mention-status.error { color: var(--kg-danger); }
.mention-empty { border-top: 1px solid var(--kg-border-subtle); }

.composer-box { padding: 9px 13px 5px; }

.send-btn {
  flex: none;
  width: 34px;
  height: 34px;
  display: grid;
  padding: 0;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-md);
  background: var(--kg-accent-gradient);
  color: var(--kg-text-on-accent);
  box-shadow: var(--kg-shadow-accent);
  cursor: pointer;
  transition: transform var(--kg-motion-fast) var(--kg-ease-standard),
    box-shadow var(--kg-motion-fast) var(--kg-ease-standard),
    filter var(--kg-motion-fast) var(--kg-ease-standard);
}

/* hover 提亮并轻微上浮；stop / disabled 态保持扁平 */
.send-btn:hover:not(:disabled):not(.stop) { filter: brightness(1.08); transform: translateY(-1px); }
.send-btn:active:not(:disabled) { transform: translateY(0); }
.send-btn.stop { border-color: var(--kg-border-default); background: var(--kg-bg-surface-2); color: var(--kg-text-secondary); box-shadow: none; }
.send-btn.stop:hover { border-color: var(--kg-danger-border); color: var(--kg-danger); }
.send-btn:disabled { border-color: var(--kg-border-subtle); background: var(--kg-bg-surface-2); color: var(--kg-text-disabled); box-shadow: none; cursor: not-allowed; }

.composer-footer {
  min-height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-3);
  padding: 0 9px 9px 13px;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.composer-meta {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
}

.composer-actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}

.composer-separator { width: 1px; height: 14px; background: var(--kg-border-subtle); }

.context-trigger { min-width: 0; max-width: 150px; height: 26px; display: inline-flex; align-items: center; gap: 6px; padding: 0 7px; border: 1px solid transparent; border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); font-size: 11px; cursor: pointer; }
.context-trigger > span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.context-trigger:hover:not(:disabled) { border-color: var(--kg-border-default); background: var(--kg-bg-surface-2); color: var(--kg-text-primary); }
.context-trigger:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }
.at-mark { font: 600 13px/1 var(--kg-font-mono); }

.workspace-control {
  min-width: 0;
  max-width: 260px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 7px;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  font: 11px/1.4 var(--kg-font-mono);
  cursor: pointer;
}
.workspace-control > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.workspace-control:hover:not(:disabled) { border-color: var(--kg-border-default); background: var(--kg-bg-surface-2); }
.workspace-control:disabled { color: var(--kg-text-tertiary); cursor: default; }
.workspace-state { flex: none; }
.workspace-control:not(.locked) .workspace-state { transform: rotate(90deg); }

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
  .workspace-control { max-width: 170px; }
}

@media (max-width: 600px) {
  .chat-inner { padding-right: 20px; padding-left: 20px; }
  .welcome { margin-top: 38px; }
  .welcome-hints {
    max-width: 320px;
    grid-template-columns: 1fr;
    margin-right: auto;
    margin-left: auto;
  }
  .hint { min-height: 50px; }
  .composer { padding-right: 12px; padding-left: 12px; }
  .composer-footer { flex-wrap: wrap; }
  .composer-meta { width: 100%; }
  .composer-actions { width: 100%; justify-content: flex-end; }
  .workspace-control { max-width: 92px; }
  .context-trigger { max-width: 100px; }
  .mention-menu { width: 100%; }
  .mention-head span { display: none; }
}
</style>
