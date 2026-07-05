<template>
  <aside class="sidebar">
    <!-- 品牌 -->
    <div class="brand">KylinGuard</div>

    <!-- 新对话 -->
    <button class="new-btn" :disabled="running" @click="newSession">
      <span class="new-icon">+</span> 新对话
    </button>

    <!-- 主导航 -->
    <nav class="nav-section">
      <button v-for="v in VIEWS" :key="v.key" class="nav-item"
              :class="{ active: view === v.key }"
              @click="$emit('change-view', v.key)">
        <span class="nav-icon">{{ v.icon }}</span>
        <span>{{ v.label }}</span>
      </button>
    </nav>

    <div class="divider"></div>

    <!-- 会话列表 -->
    <div class="section-label">历史会话</div>
    <div class="session-list">
      <button v-for="s in sessions" :key="s.id" class="session-item"
           :class="{ active: s.id === activeId }"
           @click="loadSession(s.id)">
        <span class="session-title">{{ s.title }}</span>
        <span class="session-time">{{ timeText(s.updated_at) }}</span>
      </button>
      <div v-if="!sessions.length" class="empty">暂无历史会话</div>
    </div>

    <!-- 底部用户区 -->
    <div class="user-bar">
      <span class="username">{{ username }}</span>
      <button class="logout-btn" @click="logout">退出</button>
    </div>
  </aside>
</template>

<script setup>
import { activeId, loadSession, newSession, running, sessions } from '../composables/useChat.js'
import { logout, username } from '../composables/useAuth.js'

defineProps({ view: { type: String, required: true } })
defineEmits(['change-view'])

const VIEWS = [
  { key: 'chat',      icon: '⌘', label: '对话运维' },
  { key: 'audit',     icon: '◎', label: '审计回放' },
  { key: 'policy',    icon: '⊞', label: '策略管理' },
  { key: 'dashboard', icon: '▤', label: '仪表盘'   },
  { key: 'alerts',    icon: '⚑', label: '告警配置' },
]

function timeText(ts) {
  const d = new Date(ts * 1000)
  const now = new Date()
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
</script>

<style scoped>
.sidebar {
  width: 220px; flex-shrink: 0; display: flex; flex-direction: column;
  border-right: 1px solid #1e2430; background: #0a0d12;
  font-size: 13px; overflow: hidden;
}

.brand {
  padding: 18px 16px 10px;
  font-size: 13px; font-weight: 700; letter-spacing: 0.04em;
  color: #58a6ff; font-family: ui-monospace, Consolas, monospace;
}

.new-btn {
  margin: 4px 10px 8px; padding: 7px 12px;
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  color: #c9d1d9; font-size: 12px; cursor: pointer; text-align: left;
  display: flex; align-items: center; gap: 6px; transition: background 0.15s;
}
.new-btn:hover:not(:disabled) { background: #1c2733; border-color: #58a6ff; color: #e6edf3; }
.new-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.new-icon { font-size: 15px; line-height: 1; color: #8b949e; }

.nav-section { padding: 0 8px; display: flex; flex-direction: column; gap: 1px; }
.nav-item {
  display: flex; align-items: center; gap: 9px;
  padding: 6px 10px; border-radius: 6px; border: none; background: transparent;
  color: #8b949e; font-size: 13px; cursor: pointer; width: 100%; text-align: left;
  transition: background 0.12s, color 0.12s;
}
.nav-item:hover { background: #161b22; color: #c9d1d9; }
.nav-item.active { background: #1c2733; color: #e6edf3; }
.nav-icon { font-size: 12px; width: 16px; text-align: center; flex-shrink: 0; }

.divider { height: 1px; background: #1e2430; margin: 10px 0; }

.section-label {
  padding: 0 16px 6px; font-size: 11px; color: #484f58;
  font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
}

.session-list { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.session-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 6px; width: 100%;
  border: none; background: transparent; cursor: pointer;
  color: #8b949e; font-size: 12px; text-align: left; transition: background 0.12s;
}
.session-item:hover { background: #161b22; color: #c9d1d9; }
.session-item.active { background: #1c2733; color: #e6edf3; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-time { flex-shrink: 0; color: #484f58; font-size: 11px; }
.empty { color: #484f58; font-size: 12px; padding: 12px 10px; }

.user-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-top: 1px solid #1e2430;
  font-size: 12px;
}
.username { color: #8b949e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.logout-btn {
  background: none; border: none; color: #484f58; font-size: 12px;
  cursor: pointer; padding: 2px 6px; border-radius: 4px; flex-shrink: 0;
}
.logout-btn:hover { color: #c9d1d9; background: #161b22; }
</style>
