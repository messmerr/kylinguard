<template>
  <section class="confirm-card" :class="{ high: isDouble }" aria-live="polite">
    <header class="confirm-head">
      <span class="confirm-icon"><KgIcon name="warning" :size="18" /></span>
      <div>
        <h3>{{ isDouble ? '高风险操作' : '需要确认' }}</h3>
        <p>{{ riskTitle }}</p>
      </div>
      <span class="risk-label">{{ isDouble ? '高风险' : '中风险' }}</span>
    </header>

    <div class="confirm-body">
      <div class="operation-purpose">{{ card.step.purpose }}</div>
      <div class="command-row">
        <code>{{ card.step.tool }}</code>
        <code>{{ argsText }}</code>
      </div>
      <div class="reason-row">
        <span>检查结果</span>
        <p>{{ card.decision.reason }}</p>
      </div>
    </div>

    <footer class="confirm-actions">
      <span class="audit-note">批准或拒绝的结果会写入审计记录</span>
      <div>
        <el-button size="small" :disabled="resolving" @click="act(false)">拒绝</el-button>
        <el-button :type="approveType" size="small" :loading="resolving" @click="act(true)">
          {{ isDouble ? '继续确认' : '批准执行' }}
        </el-button>
      </div>
    </footer>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import KgIcon from './KgIcon.vue'
import { resolveConfirm } from '../composables/useChat.js'

const props = defineProps({ card: { type: Object, required: true } })
const resolving = ref(false)

const isDouble = computed(() => props.card.decision.action === 'double_confirm')
const riskTitle = computed(() => (
  isDouble.value ? '该操作可能造成不可逆影响' : '该操作会修改系统状态'
))
const approveType = computed(() => (isDouble.value ? 'danger' : 'warning'))
const argsText = computed(() => JSON.stringify(props.card.step.arguments || {}))

async function act(approved) {
  if (resolving.value) return
  if (approved && isDouble.value) {
    try {
      const { value } = await ElMessageBox.prompt(
        '输入“确认执行”以继续。',
        '再次确认',
        { confirmButtonText: '执行', cancelButtonText: '取消' },
      )
      if (value !== '确认执行') return
    } catch { return }
  }
  resolving.value = true
  try {
    await resolveConfirm(props.card, approved)
  } finally {
    resolving.value = false
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
  font-size: 12px;
}
.high .risk-label { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); }

.confirm-body { padding: 14px; }
.operation-purpose { color: var(--kg-text-primary); font-size: 14px; font-weight: 500; line-height: 1.55; }
.command-row { display: grid; gap: 4px; margin-top: 10px; padding: 9px 10px; border-radius: var(--kg-radius-sm); background: var(--kg-bg-code); }
.command-row code { color: var(--kg-text-secondary); font: 12px/1.55 var(--kg-font-mono); word-break: break-all; }
.command-row code:first-child { color: var(--kg-accent); }

.reason-row { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; align-items: baseline; margin-top: 12px; }
.reason-row > span { color: var(--kg-text-tertiary); font-size: 12px; }
.reason-row p { margin: 0; color: var(--kg-text-secondary); font-size: 13px; line-height: 1.55; }

.confirm-actions {
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-top: 1px solid var(--kg-border-subtle);
}

.audit-note { color: var(--kg-text-tertiary); font-size: 12px; }
.confirm-actions > div { display: flex; gap: 8px; flex: none; }

@media (max-width: 1080px) {
  .audit-note { display: none; }
  .confirm-actions { justify-content: flex-end; }
}
</style>
