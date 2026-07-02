<template>
  <LoginView v-if="!authed" />
  <div v-else class="app-shell">
    <header class="topbar">
      <el-button text size="small" @click="showSidebar = !showSidebar">☰</el-button>
      <span class="brand">🛡 麒盾 KylinGuard</span>
      <nav class="nav">
        <a v-for="v in VIEWS" :key="v.key" class="nav-item"
           :class="{ active: view === v.key }" @click="view = v.key">
          {{ v.label }}
        </a>
      </nav>
      <el-button v-if="view === 'chat'" text size="small"
                 @click="showPanel = !showPanel">📊</el-button>
      <span class="user">{{ username }}</span>
      <el-button text size="small" @click="logout">退出</el-button>
    </header>

    <ChatView v-if="view === 'chat'"
              :show-sidebar="showSidebar" :show-panel="showPanel" />
    <AuditView v-else-if="view === 'audit'" />
    <PolicyView v-else-if="view === 'policy'" />
    <DashboardView v-else-if="view === 'dashboard'" />
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

const VIEWS = [
  { key: 'chat', label: '对话运维' },
  { key: 'audit', label: '审计回放' },
  { key: 'policy', label: '策略管理' },
  { key: 'dashboard', label: '仪表盘' },
]

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
.app-shell { height: 100%; display: flex; flex-direction: column; }

.topbar { display: flex; align-items: center; gap: 12px; padding: 8px 14px;
  border-bottom: 1px solid #21262d; flex-shrink: 0; }
.brand { font-size: 14px; font-weight: 700; color: #e6edf3; }
.nav { display: flex; gap: 4px; flex: 1; margin-left: 12px; }
.nav-item { padding: 5px 14px; border-radius: 8px; font-size: 13px;
  color: #8b949e; cursor: pointer; user-select: none; }
.nav-item:hover { background: #161b22; color: #c9d1d9; }
.nav-item.active { background: #1c2733; color: #e6edf3; }
.user { font-size: 12px; color: #8b949e; }
</style>
