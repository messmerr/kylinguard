<template>
  <el-card class="confirm-card" shadow="never">
    <template #header>
      <span class="warn">⚠ 待管理员确认</span>
      <el-tag size="small" :type="card.decision.action === 'double_confirm' ? 'danger' : 'warning'">
        {{ card.decision.action === 'double_confirm' ? '高危 · 需二次确认' : '需确认' }}
      </el-tag>
    </template>
    <p class="mono">{{ card.step.tool }} {{ JSON.stringify(card.step.arguments) }}</p>
    <p>{{ card.step.purpose }}</p>
    <p class="reason">{{ card.decision.reason }}</p>
    <div class="actions">
      <el-button type="danger" size="small" @click="act(true)">批准执行</el-button>
      <el-button size="small" @click="act(false)">拒绝</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ElMessageBox } from 'element-plus'
import { resolveConfirm } from '../composables/useChat.js'

const props = defineProps({ card: { type: Object, required: true } })

async function act(approved) {
  if (approved && props.card.decision.action === 'double_confirm') {
    try {
      const { value } = await ElMessageBox.prompt(
        '高危操作！请输入「确认执行」以二次确认', '二次确认',
        { confirmButtonText: '执行', cancelButtonText: '取消' })
      if (value !== '确认执行') return
    } catch { return }
  }
  await resolveConfirm(props.card, approved)
}
</script>

<style scoped>
.confirm-card { max-width: 680px; margin: 8px 0 8px 24px;
  border-color: #d29922; background: #14110a; }
.confirm-card :deep(.el-card__header) { display: flex; align-items: center;
  gap: 10px; padding: 10px 16px; }
.warn { color: #d29922; font-weight: 600; }
.confirm-card p { margin: 4px 0; font-size: 13px; color: #e6edf3; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; color: #79c0ff; word-break: break-all; }
.reason { color: #8b949e; font-size: 12px; }
.actions { margin-top: 10px; }
</style>
