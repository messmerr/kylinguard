<template>
  <div class="app-shell">
    <Sidebar :view="view" @change-view="changeView" />
    <section class="workspace">
      <header class="page-bar">
        <div class="page-ident">
          <h1>{{ currentView.label }}</h1>
          <span v-if="currentContext" class="page-context">{{ currentContext }}</span>
        </div>
        <div class="page-actions">
          <div v-if="fullAccessActive" class="full-access-status" role="status">
            <KgIcon name="warning" :size="14" />
            <strong>完全访问</strong>
            <span>{{ fullAccessStatusText }}</span>
            <button type="button" :disabled="revokingAccess" @click="stopFullAccess">
              {{ revokingAccess ? '正在收回' : '收回' }}
            </button>
          </div>
          <button ref="statusTrigger" class="status-trigger" :class="{ active: showPanel }"
                  type="button" aria-controls="system-status-panel"
                  aria-haspopup="dialog" :aria-expanded="showPanel"
                  aria-label="系统状态" title="系统状态" @click="toggleStatusPanel">
            <KgIcon name="gauge" :size="16" />
            <span>系统状态</span>
          </button>
        </div>
      </header>

      <div class="content-area">
        <ChatView
          v-if="view === 'chat'"
          @open-model-settings="changeView('models')"
          @open-extensions="changeView('extensions')"
        />
        <ModelSettingsView v-else-if="view === 'models'" />
        <ExtensionsView v-else-if="view === 'extensions'" />
        <AuditView v-else-if="view === 'audit'" />
        <PolicyView v-else-if="view === 'policy'" />
        <DashboardView v-else-if="view === 'dashboard'" />
        <AlertsView v-else-if="view === 'alerts'" />
      </div>

      <StatusPanel :open="showPanel" @close="closeStatusPanel" @closed="restoreStatusFocus" />
    </section>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { activeId, refreshSessions, sessions } from './composables/useChat.js'
import { loadExtensions } from './composables/useExtensions.js'
import { loadModelConfig } from './composables/useModels.js'
import {
  fullAccessActive,
  fullAccessRemainingMs,
  expirePermissionContext,
  permissionContext,
  revokeFullAccess,
} from './composables/usePermissions.js'
import AuditView from './views/AuditView.vue'
import ChatView from './views/ChatView.vue'
import DashboardView from './views/DashboardView.vue'
import AlertsView from './views/AlertsView.vue'
import ExtensionsView from './views/ExtensionsView.vue'
import ModelSettingsView from './views/ModelSettingsView.vue'
import PolicyView from './views/PolicyView.vue'
import KgIcon from './components/KgIcon.vue'
import Sidebar from './components/Sidebar.vue'
import StatusPanel from './components/StatusPanel.vue'

const VALID_VIEWS = new Set(['chat', 'models', 'extensions', 'audit', 'policy', 'dashboard', 'alerts'])
const initialView = new URLSearchParams(window.location.search).get('view')
const view = ref(VALID_VIEWS.has(initialView) ? initialView : 'chat')
const showPanel = ref(false)
const statusTrigger = ref(null)

const VIEWS = {
  chat: { label: '任务' },
  models: { label: '模型服务' },
  extensions: { label: '扩展' },
  audit: { label: '审计记录' },
  policy: { label: '权限与安全' },
  dashboard: { label: '总览' },
  alerts: { label: '告警' },
}

const currentView = computed(() => VIEWS[view.value] || VIEWS.chat)
const currentContext = computed(() => {
  if (view.value !== 'chat' || !activeId.value) return ''
  return sessions.value.find((item) => item.id === activeId.value)?.title || ''
})

const permissionNow = ref(Date.now())
const revokingAccess = ref(false)
let permissionTimer = null

watch(() => [permissionContext.mode, permissionContext.expiresAt], ([mode, expiresAt]) => {
  if (permissionTimer) clearInterval(permissionTimer)
  permissionTimer = null
  if (['full_access', 'trusted_workspace'].includes(mode) && expiresAt) {
    permissionNow.value = Date.now()
    permissionTimer = setInterval(() => {
      permissionNow.value = Date.now()
      const expiringMode = permissionContext.mode
      if (expirePermissionContext(permissionNow.value)) {
        ElMessage.info(expiringMode === 'full_access'
          ? '完全访问已到期，权限已恢复为“确认后执行”'
          : '可信目录授权已到期，后续修改会再次询问')
      }
    }, 1000)
  }
}, { immediate: true })
onUnmounted(() => permissionTimer && clearInterval(permissionTimer))

const fullAccessStatusText = computed(() => {
  const identity = permissionContext.executorIdentity || '未配置独立执行账号'
  const remaining = fullAccessRemainingMs(permissionNow.value)
  if (remaining == null) return `${identity} · 本次会话`
  if (remaining <= 0) return `${identity} · 即将到期`
  if (remaining >= 60_000) return `${identity} · 还剩 ${Math.ceil(remaining / 60_000)} 分钟`
  return `${identity} · 还剩 ${Math.ceil(remaining / 1000)} 秒`
})

async function stopFullAccess() {
  if (revokingAccess.value) return
  revokingAccess.value = true
  try {
    await revokeFullAccess()
    ElMessage.success('完全访问已收回，后续修改会再次询问')
  } catch (error) {
    ElMessage.error(error.message || '完全访问收回失败')
  } finally {
    revokingAccess.value = false
  }
}

function changeView(next) {
  if (!VALID_VIEWS.has(next)) return
  view.value = next
  showPanel.value = false
  const url = new URL(window.location.href)
  if (next === 'chat') url.searchParams.delete('view')
  else url.searchParams.set('view', next)
  window.history.replaceState(null, '', url)
}

function toggleStatusPanel() {
  if (showPanel.value) closeStatusPanel()
  else showPanel.value = true
}

function closeStatusPanel() {
  if (!showPanel.value) return
  showPanel.value = false
}

function restoreStatusFocus() {
  statusTrigger.value?.focus()
}

refreshSessions().catch(() => {})
loadExtensions().catch(() => {})
loadModelConfig().catch(() => {})
</script>

<style scoped>
.app-shell {
  height: 100%;
  display: flex;
  background: var(--kg-bg-canvas);
}

.workspace {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-bar {
  height: var(--kg-pagebar-height);
  flex: 0 0 var(--kg-pagebar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-4);
  padding: 0 var(--kg-space-5);
  border-bottom: 1px solid var(--kg-border-subtle);
  background: var(--kg-bg-surface-1);
  box-shadow: 0 1px 2px rgb(26 43 74 / 4%);
  z-index: 5;
}

.page-ident {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: var(--kg-space-3);
}

.page-ident h1 {
  flex: none;
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 22px;
}

.page-ident h1::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 14px;
  margin-right: 9px;
  border-radius: 2px;
  background: var(--kg-accent);
  vertical-align: -2px;
}

.page-context {
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-actions { display: flex; align-items: center; gap: var(--kg-space-2); }

.full-access-status {
  height: 32px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 6px 0 9px;
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
  font-size: 11px;
}

.full-access-status strong { color: var(--kg-danger); font-size: 12px; font-weight: 650; }
.full-access-status > span { color: #a74747; }
.full-access-status button {
  height: 22px;
  padding: 0 7px;
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-xs);
  background: #fff;
  color: var(--kg-text-primary);
  font-size: 11px;
  cursor: pointer;
}
.full-access-status button:hover:not(:disabled) { background: var(--kg-danger-soft); }
.full-access-status button:disabled { color: var(--kg-text-disabled); cursor: wait; }

.status-trigger {
  height: 32px;
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-tertiary);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.status-trigger:hover,
.status-trigger.active {
  border-color: #9db8f6;
  background: var(--kg-accent-soft);
  color: var(--kg-accent);
}

.content-area {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

@media (max-width: 1080px) {
  .page-bar { padding: 0 var(--kg-space-5); }
  .page-context { display: none; }
  .full-access-status > span { display: none; }
}
</style>
