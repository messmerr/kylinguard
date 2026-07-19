<template>
  <section class="confirm-card" :class="{ high: isHigh }" aria-live="polite">
    <header class="confirm-head">
      <span class="confirm-icon"><KgIcon name="warning" :size="18" /></span>
      <div>
        <h3>{{ headingText }}</h3>
        <p>{{ impactText }}</p>
      </div>
      <span class="risk-label">{{ riskLabel }}</span>
    </header>

    <div class="confirm-body">
      <div class="operation-purpose">{{ operationSummary }}</div>

      <div v-if="targetPath" class="target-row">
        <span>服务器路径</span>
        <code>{{ targetPath }}</code>
      </div>

      <div v-if="effectLabels.length" class="effect-list" aria-label="操作影响">
        <span v-for="effect in effectLabels" :key="effect">{{ effect }}</span>
      </div>

      <details class="technical-detail">
        <summary>查看操作详情</summary>
        <div class="command-row">
          <code>{{ card.step?.tool || card.operation?.tool || '未知工具' }}</code>
          <code>{{ argsText }}</code>
        </div>
        <div v-if="card.decision?.reason" class="reason-row">
          <span>检查说明</span>
          <p>{{ card.decision.reason }}</p>
        </div>
      </details>
    </div>

    <footer class="confirm-actions">
      <span class="audit-note" role="status">
        <template v-if="resolving">
          <span class="kg-spinner" aria-hidden="true"></span>正在提交你的选择…
        </template>
        <template v-else>{{ auditNote }}</template>
      </span>
      <div class="action-buttons">
        <el-button
          size="small"
          :loading="resolving === 'deny'"
          :disabled="Boolean(resolving)"
          @click="act('deny')"
        >不允许</el-button>
        <el-button
          :type="isHigh ? 'danger' : 'warning'"
          size="small"
          :loading="resolving === 'allow_once'"
          :disabled="Boolean(resolving)"
          @click="act('allow_once')"
        >{{ primaryActionLabel }}</el-button>
        <el-dropdown
          v-if="moreChoices.length"
          placement="bottom-end"
          :disabled="Boolean(resolving)"
          @command="act"
        >
          <el-button
            size="small"
            :loading="Boolean(resolving) && !['deny', 'allow_once'].includes(resolving)"
            :disabled="Boolean(resolving)"
            aria-label="更多授权方式"
          >
            更多<KgIcon name="chevron" :size="11" class="more-chevron" />
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                v-for="choice in moreChoices"
                :key="choice.id"
                :command="choice.id"
              >
                <span class="choice-copy">
                  <strong>{{ choice.label }}</strong>
                  <small>{{ choice.description }}</small>
                </span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </footer>
  </section>
</template>

<script>
const CARD_RISK_LABELS = Object.freeze({
  low: '低风险',
  medium: '中风险',
  high: '高风险',
})

export function effectiveRiskForCard(card = {}) {
  const decision = card.decision
  if (decision && typeof decision === 'object') {
    if (CARD_RISK_LABELS[decision.risk]) return decision.risk
    if (decision.action === 'double_confirm' || decision.action === 'deny') return 'high'
    if (decision.action === 'confirm') return 'medium'
    if (decision.action === 'auto') return 'low'
    return 'medium'
  }
  return CARD_RISK_LABELS[card.step?.risk] ? card.step.risk : 'medium'
}

export function isHighRiskCard(card = {}) {
  const decision = card.decision
  if (decision && typeof decision === 'object') {
    return decision.action === 'double_confirm' || decision.risk === 'high'
  }
  return card.step?.risk === 'high'
}

export function riskLabelForCard(card = {}) {
  return CARD_RISK_LABELS[effectiveRiskForCard(card)] || '需授权'
}

export function confirmationChoicesForCard(card = {}, hasTarget = false) {
  const raw = Array.isArray(card.choices) && card.choices.length
    ? card.choices
    : [
      { id: 'allow_once', label: '仅允许这一步' },
      { id: 'allow_session', label: '本次会话允许同类操作' },
      ...(hasTarget ? [{ id: 'authorize_path', label: '全局自动允许此目录' }] : []),
    ]
  const normalized = raw.map((choice) => {
    if (typeof choice === 'string') return { id: choice, label: '', description: '' }
    return {
      id: choice.id || choice.value || choice.decision,
      label: choice.label || '',
      description: choice.description || '',
    }
  })
  return isHighRiskCard(card)
    ? normalized.filter((choice) => ['deny', 'allow_once'].includes(choice.id))
    : normalized
}
</script>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import KgIcon from './KgIcon.vue'
import { resolveConfirm } from '../composables/useChat.js'

const props = defineProps({ card: { type: Object, required: true } })
const resolving = ref('')

const isHigh = computed(() => isHighRiskCard(props.card))

const riskLabel = computed(() => riskLabelForCard(props.card))
const headingText = computed(() => (
  isHigh.value ? '高风险操作 · 第 1/2 步' : '需要你的允许'
))
const auditNote = computed(() => (
  isHigh.value ? '高风险操作仅授权当前一步；你的选择会写入审计记录' : '你的选择会写入审计记录'
))
const operationSummary = computed(() => (
  props.card.operation?.summary
  || props.card.step?.purpose
  || '执行这一步操作'
))

const effects = computed(() => {
  const raw = props.card.operation?.effects || props.card.operation?.actions || []
  return Array.isArray(raw) ? raw.map(String) : raw ? [String(raw)] : []
})

const EFFECT_LABELS = {
  read: '读取', create: '新建文件', write: '写入文件', modify: '修改内容',
  delete: '删除', execute: '运行命令', control: '改变服务状态',
  elevate: '使用管理员权限', network: '访问网络',
}
const effectLabels = computed(() => effects.value.map(
  (effect) => EFFECT_LABELS[effect] || effect,
))

function resourcePath() {
  const resources = props.card.operation?.resources || []
  const pathResource = resources.find((resource) => (
    resource?.kind === 'path' || resource?.path
  ))
  if (pathResource) return pathResource.path || pathResource.value || ''
  const args = props.card.step?.arguments || props.card.operation?.arguments || {}
  return args.path || args.file || args.filename || args.target || args.destination || ''
}

const targetPath = computed(() => String(resourcePath() || ''))
const argsText = computed(() => JSON.stringify(
  props.card.step?.arguments || props.card.operation?.arguments || {},
))

const impactText = computed(() => {
  if (isHigh.value) return '请先核对目标和影响范围，再进行最终确认'
  if (targetPath.value) return '这一步会修改服务器上的内容'
  return '这一步会改变系统当前状态'
})

const operationActionLabel = computed(() => {
  const tool = String(props.card.step?.tool || props.card.operation?.tool || '')
  if (effects.value.includes('delete') || tool.includes('clean') || tool.includes('delete')) return '允许删除'
  if (tool.includes('restart')) return '允许重启'
  if (tool.includes('stop')) return '允许停止'
  if (effects.value.includes('create') || tool.includes('write_file')) return '允许创建'
  if (effects.value.includes('modify') || effects.value.includes('write')) return '允许修改'
  return '仅允许这一步'
})
const primaryActionLabel = computed(() => (
  isHigh.value ? '继续最终确认' : operationActionLabel.value
))

function normalizedChoices() {
  return confirmationChoicesForCard(props.card, Boolean(targetPath.value))
}

const moreChoices = computed(() => normalizedChoices()
  .filter((choice) => !['deny', 'allow_once'].includes(choice.id))
  .map((choice) => ({
    ...choice,
    label: choice.label || (choice.id === 'authorize_path' ? '全局自动允许此目录' : '本次会话允许同类操作'),
    description: choice.description || (choice.id === 'authorize_path'
      ? '加入全局自动执行范围，对所有任务生效；仍需通过 Reviewer 且不会放行高风险操作'
      : '关闭本次会话或授权到期后自动收回'),
  })))

function suggestedDirectory() {
  const proposed = props.card.operation?.suggested_scope?.path
    || props.card.operation?.auto_review_root
  if (proposed) return proposed
  const path = targetPath.value
  if (!path) return ''
  const tool = String(props.card.step?.tool || props.card.operation?.tool || '')
  if (tool.includes('write_file') || tool.includes('edit_file') || tool.includes('create')) {
    return path.slice(0, path.lastIndexOf('/')) || '/'
  }
  return path
}

function decisionScope(decision) {
  if (decision === 'allow_once' || decision === 'deny') return null
  const path = suggestedDirectory()
  if (decision === 'authorize_path' && !path) return null
  return {
    kind: path ? 'path' : 'operation',
    ...(path ? { path } : {}),
    actions: effects.value.length ? effects.value : ['create', 'modify'],
    recursive: Boolean(path),
    lifetime: 'session',
  }
}

async function confirmHighRisk() {
  await ElMessageBox.confirm(
    `${operationSummary.value}${targetPath.value ? `\n目标：${targetPath.value}` : ''}\n本次仅授权当前动作。`,
    '高风险操作 · 第 2/2 步',
    {
      confirmButtonText: operationActionLabel.value,
      cancelButtonText: '返回检查',
      type: 'warning',
      distinguishCancelAndClose: true,
    },
  )
}

async function act(decision) {
  if (resolving.value) return
  if (decision === 'authorize_path' && !suggestedDirectory()) {
    ElMessage.warning('这一步没有可授权的服务器目录')
    return
  }
  try {
    if (decision !== 'deny' && isHigh.value) await confirmHighRisk()
    resolving.value = decision
    const scope = decisionScope(decision) || {}
    await resolveConfirm(props.card, decision, scope)
  } catch (error) {
    resolving.value = ''
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel') return
    ElMessage.error(error.message || '权限处理失败，请重试')
  }
}
</script>

<style scoped>
.confirm-card {
  max-width: 720px;
  margin: 14px 0 14px 39px;
  overflow: hidden;
  border: 1px solid var(--kg-warning-border);
  border-left: 3px solid var(--kg-warning);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-md);
}

.confirm-card.high { border-color: var(--kg-danger-border); border-left-color: var(--kg-danger); }

.confirm-head {
  min-height: 64px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

/* 高危确认：头部整体转为 danger 语义，与左侧 3px 色条呼应 */
.high .confirm-head { border-bottom-color: var(--kg-danger-border); background: var(--kg-danger-soft); }

.confirm-icon {
  width: 34px;
  height: 34px;
  display: grid;
  flex: none;
  place-items: center;
  border-radius: var(--kg-radius-md);
  background: var(--kg-warning-soft);
  color: var(--kg-warning);
}
.high .confirm-icon { background: var(--kg-danger-soft); color: var(--kg-danger); }

.confirm-head > div { min-width: 0; flex: 1; }
.confirm-head h3 { margin: 0; color: var(--kg-text-primary); font-size: 14px; font-weight: 600; }
.confirm-head p { margin: 2px 0 0; color: var(--kg-text-tertiary); font-size: 12px; }

.risk-label {
  flex: none;
  padding: 2px 7px;
  border: 1px solid var(--kg-warning-border);
  border-radius: var(--kg-radius-xs);
  background: var(--kg-warning-soft);
  color: var(--kg-warning);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .02em;
}
.high .risk-label { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); }

.confirm-body { padding: 14px; }
.operation-purpose { color: var(--kg-text-primary); font-size: 14px; font-weight: 550; line-height: 1.55; }

.target-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
  margin-top: 11px;
}
.target-row span { color: var(--kg-text-tertiary); font-size: 11px; }
.target-row code { color: var(--kg-text-secondary); font: 12px/1.5 var(--kg-font-mono); overflow-wrap: anywhere; }

.effect-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 11px; }
.effect-list span {
  padding: 2px 6px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-xs);
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.technical-detail { margin-top: 12px; color: var(--kg-text-tertiary); font-size: 11px; }
.technical-detail summary { width: fit-content; cursor: pointer; user-select: none; }
.technical-detail[open] summary { color: var(--kg-text-secondary); }
.command-row { display: grid; gap: 4px; margin-top: 9px; padding: 9px 10px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-2); }
.command-row code { color: var(--kg-text-secondary); font: 12px/1.55 var(--kg-font-mono); word-break: break-all; }
.command-row code:first-child { color: var(--kg-accent); }

.reason-row { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; align-items: baseline; margin-top: 10px; }
.reason-row > span { color: var(--kg-text-tertiary); font-size: 11px; }
.reason-row p { margin: 0; color: var(--kg-text-secondary); font-size: 12px; line-height: 1.55; }

.confirm-actions {
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-top: 1px solid var(--kg-border-subtle);
  background: var(--kg-bg-surface-2);
}

.audit-note { display: inline-flex; align-items: center; gap: 6px; color: var(--kg-text-tertiary); font-size: 11px; }
.audit-note .kg-spinner { width: 11px; height: 11px; border-width: 1px; }
.action-buttons { display: flex; align-items: center; gap: 8px; flex: none; }
.more-chevron { display: inline-block; margin-left: 5px; transform: rotate(90deg); }
.choice-copy { display: grid; gap: 2px; padding: 3px 0; }
.choice-copy strong { color: var(--kg-text-primary); font-size: 12px; font-weight: 550; }
.choice-copy small { color: var(--kg-text-tertiary); font-size: 11px; }

@media (max-width: 1080px) {
  .audit-note { display: none; }
  .confirm-actions { justify-content: flex-end; }
}
</style>
