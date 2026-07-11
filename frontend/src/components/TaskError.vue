<template>
  <section class="task-error" aria-live="assertive">
    <span class="error-icon"><KgIcon name="warning" :size="17" /></span>
    <div class="error-content">
      <h3>{{ title }}</h3>
      <p>{{ error.message }}</p>
      <p v-if="answerDetail" class="error-answer">{{ answerDetail }}</p>
      <div v-if="meta.length" class="error-meta">
        <code v-for="entry in meta" :key="entry">{{ entry }}</code>
      </div>
      <div class="error-actions">
        <button v-if="showModelSettings" type="button"
                :disabled="disabled" @click="$emit('configure-model')">
          <KgIcon name="model" :size="13" />检查模型设置
        </button>
        <button v-if="error.retryable !== false" type="button"
                :disabled="disabled" @click="$emit('retry')">
          <KgIcon name="refresh" :size="13" />重新尝试
        </button>
        <button type="button" @click="copyDetails">{{ copied ? '已复制' : '复制错误详情' }}</button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import KgIcon from './KgIcon.vue'

const props = defineProps({
  item: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
defineEmits(['retry', 'configure-model'])

const copied = ref(false)
const error = computed(() => props.item.error || {})
const showModelSettings = computed(() => new Set([
  'llm_config_missing', 'llm_auth_invalid', 'llm_forbidden',
  'llm_model_not_found', 'llm_provider_unavailable',
]).has(error.value.code))
const answerDetail = computed(() => {
  const answer = String(props.item.answer || '').trim()
  const message = String(error.value.message || '').trim()
  if (!answer) return ''
  if (!message || !answer.startsWith(message)) return answer
  const remainder = answer.slice(message.length).trim()
  // 后端失败终态通常只重复“任务已中止 + incident”；这些信息已经由标题
  // 与下方 meta 完整表达，重复展示会让错误卡显得啰嗦。
  if (!remainder || /^任务已中止。?\s*错误编号[:：]/.test(remainder)) return ''
  return remainder
})
const title = computed(() => ({
  planning: '规划未能完成',
  reviewing: '安全检查未能完成',
  executing: '操作执行失败',
  internal: '任务未能完成',
  request: '请求未能完成',
}[error.value.stage] || '连接已中断'))
const meta = computed(() => [
  error.value.httpStatus ? `HTTP ${error.value.httpStatus}` : '',
  error.value.code && error.value.code !== 'request_failed' ? error.value.code : '',
  error.value.requestId ? `request ${error.value.requestId}` : '',
  error.value.incidentId ? `incident ${error.value.incidentId}` : '',
  props.item.elapsedMs != null ? `用时 ${formatDuration(props.item.elapsedMs)}` : '',
].filter(Boolean))

function formatDuration(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

async function copyDetails() {
  const detail = JSON.stringify({
    message: error.value.message,
    code: error.value.code,
    stage: error.value.stage,
    http_status: error.value.httpStatus,
    request_id: error.value.requestId,
    incident_id: error.value.incidentId,
    detail: error.value.detail,
    elapsed_ms: props.item.elapsedMs,
    answer: props.item.answer,
  }, null, 2)
  try {
    await navigator.clipboard.writeText(detail)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1600)
  } catch { /* 浏览器未授权剪贴板时保持原按钮状态 */ }
}
</script>

<style scoped>
.task-error {
  display: flex;
  gap: 11px;
  margin: 12px 0 14px 42px;
  padding: 12px 13px;
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-danger-soft);
}
.error-icon { flex: none; padding-top: 1px; color: var(--kg-danger); }
.error-content { min-width: 0; flex: 1; }
.error-content h3 { margin: 0; color: var(--kg-text-primary); font-size: 13px; font-weight: 600; }
.error-content p { margin: 5px 0 0; color: var(--kg-text-secondary); font-size: 13px; line-height: 1.55; }
.error-content .error-answer { color: var(--kg-text-tertiary); }
.error-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.error-meta code { padding: 2px 5px; border-radius: var(--kg-radius-xs); background: var(--kg-bg-code); color: var(--kg-text-tertiary); font: 11px/1.4 var(--kg-font-mono); }
.error-actions { display: flex; gap: 8px; margin-top: 10px; }
.error-actions button { display: inline-flex; align-items: center; gap: 5px; padding: 4px 8px; border: 1px solid var(--kg-border-default); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-1); color: var(--kg-text-secondary); font-size: 12px; cursor: pointer; }
.error-actions button:hover:not(:disabled) { color: var(--kg-text-primary); border-color: var(--kg-border-strong); }
.error-actions button:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }
</style>
