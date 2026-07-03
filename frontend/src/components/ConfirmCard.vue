<template>
  <el-card class="confirm-card" shadow="never">
    <template #header>
      <div class="head">
        <div>
          <div class="eyebrow">待管理员裁决</div>
          <div class="title">{{ riskTitle }}</div>
        </div>
        <el-tag size="small" :type="tagType">{{ actionLabel }}</el-tag>
      </div>
    </template>

    <div class="summary">
      <div class="row">
        <span class="label">工具</span>
        <code>{{ card.step.tool }}</code>
      </div>
      <div class="row">
        <span class="label">参数</span>
        <code>{{ argsText }}</code>
      </div>
      <div class="row">
        <span class="label">目的</span>
        <span>{{ card.step.purpose }}</span>
      </div>
      <div class="row">
        <span class="label">门控</span>
        <span>{{ card.decision.reason }}</span>
      </div>
    </div>

    <div class="guard-note">
      只有合法的中高危结构化操作会进入确认；命中红线的危险命令会被直接拒绝。
    </div>

    <div class="actions">
      <el-button :type="approveType" size="small" @click="act(true)">
        批准执行
      </el-button>
      <el-button size="small" @click="act(false)">拒绝</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import { resolveConfirm } from '../composables/useChat.js'

const props = defineProps({ card: { type: Object, required: true } })

const isDouble = computed(() => props.card.decision.action === 'double_confirm')
const actionLabel = computed(() => isDouble.value ? '高危二次确认' : '中危确认')
const riskTitle = computed(() => isDouble.value
  ? '该操作可能造成不可逆影响'
  : '该操作会修改系统状态')
const tagType = computed(() => isDouble.value ? 'danger' : 'warning')
const approveType = computed(() => isDouble.value ? 'danger' : 'warning')
const argsText = computed(() => JSON.stringify(props.card.step.arguments || {}))

async function act(approved) {
  if (approved && isDouble.value) {
    try {
      const { value } = await ElMessageBox.prompt(
        '高危操作需要二次确认。请输入“确认执行”后才会继续。',
        '二次确认',
        { confirmButtonText: '执行', cancelButtonText: '取消' })
      if (value !== '确认执行') return
    } catch { return }
  }
  await resolveConfirm(props.card, approved)
}
</script>

<style scoped>
.confirm-card { max-width: 720px; margin: 10px 0 10px 24px;
  border-color: #d29922; background: #14110a; border-radius: 8px; }
.confirm-card :deep(.el-card__header) { padding: 12px 16px; }
.head { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; }
.eyebrow { color: #d29922; font-size: 12px; font-weight: 700; }
.title { color: #e6edf3; font-size: 14px; margin-top: 2px; }
.summary { display: grid; gap: 7px; }
.row { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 10px;
  font-size: 13px; color: #e6edf3; line-height: 1.5; }
.label { color: #8b949e; }
code { color: #79c0ff; font-size: 12px; word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.guard-note { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #0d1117; color: #8b949e; font-size: 12px; }
.actions { margin-top: 12px; display: flex; gap: 8px; }
</style>
