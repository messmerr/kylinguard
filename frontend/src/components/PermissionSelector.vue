<template>
  <el-dropdown
    trigger="click"
    placement="top-start"
    :disabled="disabled || changing"
    popper-class="kg-permission-menu"
    @command="chooseMode"
  >
    <button
      type="button"
      class="permission-trigger"
      :class="[`is-${permissionModeMeta.tone}`, { active: fullAccessActive }]"
      :disabled="disabled || changing"
      :aria-label="changing ? '正在更新全局权限' : `全局权限：${permissionModeMeta.label}`"
    >
      <span v-if="changing" class="kg-spinner" aria-hidden="true"></span>
      <KgIcon v-else :name="fullAccessActive ? 'warning' : 'shield'" :size="13" />
      <span>{{ changing ? '正在更新权限…' : `全局权限：${permissionModeMeta.label}` }}</span>
      <span v-if="fullAccessActive && permissionContext.grantsRoot" class="root-badge">ROOT</span>
      <KgIcon name="chevron" :size="11" class="select-chevron" />
    </button>

    <template #dropdown>
      <el-dropdown-menu class="permission-menu">
        <el-dropdown-item
          v-for="mode in visiblePermissionModes"
          :key="mode.value"
          :command="mode.value"
          :disabled="mode.value === 'full_access'
            && !permissionContext.fullAccessAvailable"
          :class="{ selected: mode.value === permissionMode }"
        >
          <span class="mode-check">
            <KgIcon v-if="mode.value === permissionMode" name="check" :size="13" />
          </span>
          <span class="mode-copy">
            <strong>{{ mode.label }}</strong>
            <small>
              {{ mode.value === 'full_access' && !permissionContext.fullAccessAvailable
                ? (permissionContext.fullAccessUnavailableReason || '服务端未开放')
                : mode.value === 'full_access' && permissionContext.grantsRoot
                ? '完整能力 · 将获得 root 权限'
                : mode.value === 'auto_review' && autoReviewRoots.length
                ? `自动范围 ${autoReviewRoots.length} 个目录`
                : mode.short }}
            </small>
          </span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import KgIcon from './KgIcon.vue'
import { setChatPermissionMode } from '../composables/useChat.js'
import { confirmFullAccessEnable } from '../utils/fullAccessWarnings.js'
import {
  autoReviewRoots,
  fullAccessActive,
  permissionContext,
  permissionMode,
  permissionModeMeta,
  visiblePermissionModes,
} from '../composables/usePermissions.js'

defineProps({ disabled: { type: Boolean, default: false } })
const changing = ref(false)

async function chooseMode(mode) {
  if (changing.value || mode === permissionMode.value) return
  changing.value = true
  try {
    let result = null
    if (permissionMode.value !== mode) {
      if (mode === 'full_access') {
        await confirmFullAccessEnable({
          executorIdentity: permissionContext.executorIdentity,
          grantsRoot: permissionContext.grantsRoot,
        })
      }
      result = await setChatPermissionMode(mode)
    }
    if (!result.supported) {
      ElMessage.warning('当前后端未保存全局权限设置')
    } else {
      ElMessage.success(mode === 'full_access'
        ? '完整执行能力已开启，将持续生效直至手动收回'
        : '全局权限已更新')
    }
  } catch (error) {
    // Element Plus 取消弹窗使用字符串/对象拒绝，不应显示成错误。
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel') return
    ElMessage.error(error.message || '权限修改失败')
  } finally {
    changing.value = false
  }
}
</script>

<style scoped>
.permission-trigger {
  min-height: 26px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 7px;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.permission-trigger:hover:not(:disabled) {
  border-color: var(--kg-border-subtle);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-secondary);
}

.permission-trigger.is-safe { color: var(--kg-accent); }
.permission-trigger.is-warning { color: var(--kg-warning); }
.permission-trigger.is-danger,
.permission-trigger.active {
  border-color: var(--kg-danger-border);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
}
.permission-trigger:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }
.permission-trigger .kg-spinner { width: 12px; height: 12px; border-width: 1px; }
.root-badge {
  padding: 1px 4px;
  border: 1px solid currentColor;
  border-radius: 3px;
  font: 700 9px/1.2 var(--kg-font-mono);
  letter-spacing: 0;
}
.select-chevron { transform: rotate(90deg); }

.mode-check {
  width: 16px;
  display: grid;
  flex: none;
  place-items: center;
  color: var(--kg-accent);
}

.mode-copy {
  min-width: 0;
  display: grid;
  gap: 2px;
  padding: 4px 0;
}

.mode-copy strong { color: var(--kg-text-primary); font-size: 12px; font-weight: 550; }
.mode-copy small { color: var(--kg-text-tertiary); font-size: 11px; }
</style>

<style>
.kg-permission-menu.el-popper { width: 270px; }
.kg-permission-menu .el-dropdown-menu { padding: 5px; }
.kg-permission-menu .el-dropdown-menu__item {
  align-items: flex-start;
  gap: 7px;
  min-height: 46px;
  padding: 5px 8px;
  border-radius: var(--kg-radius-sm);
  line-height: normal;
}
.kg-permission-menu .el-dropdown-menu__item.selected { background: var(--kg-selection); }
</style>
