<template>
  <LoginView v-if="!authed" />
  <div v-else class="app-shell">
    <Sidebar v-if="showSidebar" :view="view" @change-view="view = $event" />
    <div class="content-area">
      <ChatView v-if="view === 'chat'" :show-sidebar="false" :show-panel="showPanel" />
      <AuditView v-else-if="view === 'audit'" />
      <PolicyView v-else-if="view === 'policy'" />
      <DashboardView v-else-if="view === 'dashboard'" />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { authed, logout, username } from './composables/useAuth.js'
import { refreshSessions } from './composables/useChat.js'
import AuditView from './views/AuditView.vue'
import ChatView from './views/ChatView.vue'
import DashboardView from './views/DashboardView.vue'
import LoginView from './views/LoginView.vue'
import PolicyView from './views/PolicyView.vue'
import Sidebar from './components/Sidebar.vue'

const view = ref('chat')
const showSidebar = ref(true)
const showPanel = ref(true)

watch(authed, (v) => {
  if (v) refreshSessions()
}, { immediate: true })
</script>

<style>
html, body, #app { height: 100%; margin: 0; background: #0d1117;
  color: #e6edf3; font-family: -apple-system, "Segoe UI", "Microsoft YaHei",
  sans-serif; }
.app-shell { height: 100%; display: flex; flex-direction: row; }
.content-area { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
</style>
