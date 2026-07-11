<template>
  <LoginView v-if="!authed" />
  <div v-else class="app-shell">
    <Sidebar :view="view" :inert="showPanel" @change-view="changeView" />
    <section class="workspace">
      <header class="page-bar" :inert="showPanel">
        <div class="page-ident">
          <h1>{{ currentView.label }}</h1>
          <span v-if="currentContext" class="page-context">{{ currentContext }}</span>
        </div>
        <button ref="statusTrigger" class="status-trigger" :class="{ active: showPanel }"
                type="button" aria-controls="system-status-panel"
                :aria-expanded="showPanel" @click="toggleStatusPanel">
          <KgIcon name="server" :size="16" />
          <span>系统状态</span>
        </button>
      </header>

      <div class="content-area" :inert="showPanel">
        <ChatView v-if="view === 'chat'" />
        <AuditView v-else-if="view === 'audit'" />
        <PolicyView v-else-if="view === 'policy'" />
        <DashboardView v-else-if="view === 'dashboard'" />
        <AlertsView v-else-if="view === 'alerts'" />
      </div>

      <Transition name="inspector-fade">
        <button v-if="showPanel" class="inspector-scrim" type="button"
                aria-label="关闭系统状态" @click="closeStatusPanel"></button>
      </Transition>
      <StatusPanel :open="showPanel" @close="closeStatusPanel" />
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { authed } from './composables/useAuth.js'
import { activeId, refreshSessions, sessions } from './composables/useChat.js'
import AuditView from './views/AuditView.vue'
import ChatView from './views/ChatView.vue'
import DashboardView from './views/DashboardView.vue'
import LoginView from './views/LoginView.vue'
import AlertsView from './views/AlertsView.vue'
import PolicyView from './views/PolicyView.vue'
import KgIcon from './components/KgIcon.vue'
import Sidebar from './components/Sidebar.vue'
import StatusPanel from './components/StatusPanel.vue'

const view = ref('chat')
const showPanel = ref(false)
const statusTrigger = ref(null)

const VIEWS = {
  chat: { label: '任务' },
  audit: { label: '审计记录' },
  policy: { label: '安全策略' },
  dashboard: { label: '总览' },
  alerts: { label: '告警' },
}

const currentView = computed(() => VIEWS[view.value] || VIEWS.chat)
const currentContext = computed(() => {
  if (view.value !== 'chat' || !activeId.value) return ''
  return sessions.value.find((item) => item.id === activeId.value)?.title || ''
})

function changeView(next) {
  view.value = next
  showPanel.value = false
}

function toggleStatusPanel() {
  if (showPanel.value) closeStatusPanel()
  else showPanel.value = true
}

function closeStatusPanel() {
  if (!showPanel.value) return
  showPanel.value = false
  requestAnimationFrame(() => statusTrigger.value?.focus())
}

watch(authed, (v) => {
  if (v) refreshSessions()
}, { immediate: true })
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
  padding: 0 var(--kg-space-6);
  border-bottom: 1px solid var(--kg-border-subtle);
  background: rgb(16 19 20 / 92%);
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
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
}

.page-context {
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-trigger {
  height: 32px;
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.status-trigger:hover,
.status-trigger.active {
  border-color: var(--kg-border-default);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-primary);
}

.content-area {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.inspector-scrim {
  position: absolute;
  inset: var(--kg-pagebar-height) 0 0;
  z-index: 19;
  border: 0;
  background: rgb(0 0 0 / 24%);
  cursor: default;
}

.inspector-fade-enter-active,
.inspector-fade-leave-active {
  transition: opacity var(--kg-motion-base) var(--kg-ease-standard);
}

.inspector-fade-enter-from,
.inspector-fade-leave-to {
  opacity: 0;
}

@media (max-width: 1080px) {
  .page-bar { padding: 0 var(--kg-space-5); }
  .page-context { display: none; }
}
</style>
