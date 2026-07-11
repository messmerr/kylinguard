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
      :aria-label="`当前权限：${permissionModeMeta.label}`"
    >
      <span v-if="changing" class="kg-spinner" aria-hidden="true"></span>
      <KgIcon v-else :name="fullAccessActive ? 'warning' : 'shield'" :size="13" />
      <span>权限：{{ permissionModeMeta.label }}</span>
      <KgIcon name="chevron" :size="11" class="select-chevron" />
    </button>

    <template #dropdown>
      <el-dropdown-menu class="permission-menu">
        <el-dropdown-item
          v-for="mode in PERMISSION_MODES"
          :key="mode.value"
          :command="mode.value"
          :disabled="mode.value === 'full_access'
            && (!permissionContext.fullAccessAvailable || !permissionContext.sessionId)"
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
                : mode.value === 'full_access' && !permissionContext.sessionId
                ? '发送第一条消息后可开启'
                : mode.value === 'trusted_workspace' && trustedRoots.length
                ? `已信任 ${trustedRoots.length} 个目录`
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
import { ElMessage, ElMessageBox } from 'element-plus'
import KgIcon from './KgIcon.vue'
import {
  PERMISSION_MODES,
  addTrustedRoot,
  fullAccessActive,
  fullAccessDurationMinutes,
  permissionContext,
  permissionMode,
  permissionModeMeta,
  setPermissionMode,
  trustedRoots,
} from '../composables/usePermissions.js'

defineProps({ disabled: { type: Boolean, default: false } })
const changing = ref(false)

async function requestTrustedRoot() {
  const { value } = await ElMessageBox.prompt(
    '输入 Agent 可以直接创建和修改文件的服务器目录。删除操作仍会询问。',
    '添加可信目录',
    {
      inputPlaceholder: '例如 /srv/project/docs',
      confirmButtonText: '添加目录',
      cancelButtonText: '取消',
      inputValidator: (value) => (
        String(value || '').trim().startsWith('/') || '请输入以 / 开头的服务器绝对路径'
      ),
    },
  )
  return String(value || '').trim()
}

async function requestFullAccessPassword() {
  const { value } = await ElMessageBox.prompt(
    `完全访问将跳过逐项确认，并以“${permissionContext.executorIdentity}”的系统权限运行。硬红线与独立安全复核仍然生效，所有操作仍会记录。`,
    '开启完全访问',
    {
      inputType: 'password',
      inputPlaceholder: '输入当前登录密码',
      confirmButtonText: `开启 ${fullAccessDurationMinutes.value} 分钟`,
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
      inputValidator: (value) => Boolean(String(value || '').trim()) || '请输入密码',
    },
  )
  return String(value || '')
}

async function chooseMode(mode) {
  if (changing.value || mode === permissionMode.value) return
  changing.value = true
  try {
    let password = ''
    let result = null
    if (mode === 'trusted_workspace' && !trustedRoots.value.length) {
      const path = await requestTrustedRoot()
      result = await addTrustedRoot(path)
    }
    if (mode === 'full_access') password = await requestFullAccessPassword()
    if (permissionMode.value !== mode) {
      result = await setPermissionMode(mode, {
        password, durationMinutes: fullAccessDurationMinutes.value,
      })
    }
    if (!result.supported && permissionContext.sessionId) {
      ElMessage.warning('当前后端尚未保存该设置；新协议接入后会自动同步')
    } else {
      ElMessage.success(mode === 'full_access'
        ? `完全访问已开启，将在 ${fullAccessDurationMinutes.value} 分钟后收回`
        : '权限已更新')
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
