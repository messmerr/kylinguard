<template>
  <aside class="sidebar">
    <div class="side-head">
      <span class="brand">🛡 麒盾</span>
    </div>
    <el-button class="new-btn" :disabled="running" @click="newSession">
      ＋ 新对话
    </el-button>
    <div class="session-list">
      <div v-for="s in sessions" :key="s.id" class="session-item"
           :class="{ active: s.id === activeId }"
           @click="loadSession(s.id)">
        <span class="session-title">{{ s.title }}</span>
        <span class="session-time">{{ timeText(s.updated_at) }}</span>
      </div>
      <div v-if="!sessions.length" class="empty">暂无历史会话</div>
    </div>
  </aside>
</template>

<script setup>
import { activeId, loadSession, newSession, running, sessions } from '../composables/useChat.js'

function timeText(ts) {
  const d = new Date(ts * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  return sameDay
    ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
</script>

<style scoped>
.sidebar { width: 230px; flex-shrink: 0; display: flex; flex-direction: column;
  border-right: 1px solid #21262d; background: #010409; }
.side-head { padding: 14px 16px 8px; }
.brand { font-size: 16px; font-weight: 700; color: #e6edf3; }
.new-btn { margin: 6px 12px 10px; }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
.session-item { display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  color: #c9d1d9; font-size: 13px; }
.session-item:hover { background: #161b22; }
.session-item.active { background: #1c2733; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.session-time { font-size: 11px; color: #8b949e; flex-shrink: 0; }
.empty { color: #484f58; font-size: 12px; text-align: center; padding: 20px 0; }
</style>
