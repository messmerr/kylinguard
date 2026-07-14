<template>
  <aside ref="sidebar" class="sidebar" aria-label="主导航" @keydown.esc="historyOpen = false">
    <div class="brand">
      <span class="brand-mark"><KgLogo :size="22" /></span>
      <span class="brand-copy">
        <strong>麒盾</strong>
        <span>KylinGuard</span>
      </span>
    </div>

    <button class="new-btn" type="button" :disabled="running"
            title="新建任务" @click="startTask">
      <KgIcon name="plus" :size="16" />
      <span class="new-label">新建任务</span>
    </button>

    <nav class="nav-section" aria-label="工作区">
      <button v-for="v in VIEWS" :key="v.key" class="nav-item"
              :class="{ active: view === v.key }" type="button"
              :title="v.label" :aria-current="view === v.key ? 'page' : undefined"
              @click="navigate(v.key)">
        <KgIcon :name="v.icon" :size="16" />
        <span class="nav-label">{{ v.label }}</span>
      </button>
    </nav>

    <button class="rail-history" :class="{ active: historyOpen }" type="button"
            title="最近任务" aria-controls="recent-tasks" :aria-expanded="historyOpen"
            @click="historyOpen = !historyOpen">
      <KgIcon name="audit" :size="16" />
      <span class="sr-only">最近任务</span>
    </button>

    <div class="divider"></div>

    <section id="recent-tasks" class="session-section" :class="{ open: historyOpen }"
             aria-labelledby="recent-title">
      <div id="recent-title" class="section-label">最近任务</div>
      <div class="session-list">
        <button v-for="s in sessions" :key="s.id" class="session-item"
                :class="{ active: view === 'chat' && s.id === activeId }"
                type="button" :title="s.title" @click="openSession(s.id)">
          <span class="session-title">{{ s.title }}</span>
          <time class="session-time">{{ timeText(s.updated_at) }}</time>
        </button>
        <div v-if="!sessions.length" class="session-empty">还没有任务记录</div>
      </div>
    </section>

  </aside>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { activeId, loadSession, newSession, running, sessions } from '../composables/useChat.js'
import { formatRelativeTime } from '../utils/relativeTime.js'
import KgIcon from './KgIcon.vue'
import KgLogo from './KgLogo.vue'

defineProps({ view: { type: String, required: true } })
const emit = defineEmits(['change-view'])
const historyOpen = ref(false)
const sidebar = ref(null)
const relativeClock = ref(Date.now())
let relativeClockTimer = null

const VIEWS = [
  { key: 'chat', icon: 'task', label: '任务' },
  { key: 'models', icon: 'model', label: '模型服务' },
  { key: 'extensions', icon: 'server', label: '扩展' },
  { key: 'audit', icon: 'audit', label: '审计记录' },
  { key: 'policy', icon: 'shield', label: '权限与安全' },
  { key: 'dashboard', icon: 'dashboard', label: '总览' },
  { key: 'alerts', icon: 'bell', label: '告警' },
]

function startTask() {
  historyOpen.value = false
  emit('change-view', 'chat')
  newSession()
}

function openSession(id) {
  historyOpen.value = false
  emit('change-view', 'chat')
  loadSession(id)
}

function navigate(next) {
  historyOpen.value = false
  emit('change-view', next)
}

function closeHistoryOnOutside(event) {
  if (historyOpen.value && !sidebar.value?.contains(event.target)) historyOpen.value = false
}

onMounted(() => {
  document.addEventListener('pointerdown', closeHistoryOnOutside)
  relativeClockTimer = setInterval(() => { relativeClock.value = Date.now() }, 30_000)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', closeHistoryOnOutside)
  clearInterval(relativeClockTimer)
})

function timeText(ts) {
  return formatRelativeTime(ts, relativeClock.value)
}
</script>

<style scoped>
.sidebar {
  width: var(--kg-sidebar-width);
  min-width: var(--kg-sidebar-width);
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid #dfe6f5;
  background: var(--kg-bg-sidebar);
  color: var(--kg-text-secondary);
  transition: width var(--kg-motion-base) var(--kg-ease-standard),
    min-width var(--kg-motion-base) var(--kg-ease-standard);
}

.brand {
  height: var(--kg-pagebar-height);
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
  padding: 0 var(--kg-space-4);
  border-bottom: 1px solid #dfe6f5;
}

.brand-mark {
  width: 30px;
  height: 30px;
  display: grid;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: var(--kg-accent);
  color: #fff;
  box-shadow: 0 4px 12px rgb(23 92 255 / 18%);
}

.brand-copy {
  min-width: 0;
  display: grid;
  line-height: 1.15;
}

.brand-copy strong {
  color: #1046c7;
  font-size: 16px;
  font-weight: 700;
}

.brand-copy span {
  margin-top: 3px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  letter-spacing: 0;
}

.new-btn {
  height: 34px;
  margin: var(--kg-space-3) var(--kg-space-3) var(--kg-space-2);
  padding: 0 var(--kg-space-3);
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  flex: none;
  border: 1px solid #a8c0ff;
  border-radius: var(--kg-radius-sm);
  background: #fff;
  color: var(--kg-accent);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  box-shadow: 0 2px 8px rgb(23 92 255 / 7%);
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast);
}

.new-btn:hover:not(:disabled) {
  border-color: var(--kg-accent);
  background: var(--kg-accent-soft);
  color: var(--kg-accent-hover);
}

.new-btn:active:not(:disabled) { background: var(--kg-bg-surface-3); }
.new-btn:disabled {
  border-color: var(--kg-border-subtle);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-disabled);
  cursor: not-allowed;
}

.nav-section {
  display: grid;
  gap: 4px;
  padding: 0 var(--kg-space-3);
}

.nav-item {
  position: relative;
  width: 100%;
  height: 36px;
  padding: 0 var(--kg-space-3);
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  border: 1px solid transparent;
  color: var(--kg-text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast);
}

.nav-item:hover {
  background: rgb(255 255 255 / 68%);
  color: var(--kg-text-primary);
}

.nav-item.active {
  border-color: #9db9ff;
  background: #fff;
  color: var(--kg-accent);
  box-shadow: 0 3px 10px rgb(23 92 255 / 8%);
}

.nav-item.active :deep(.kg-icon) { color: var(--kg-accent); }

.rail-history { display: none; }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.divider {
  height: 1px;
  flex: none;
  margin: var(--kg-space-3) var(--kg-space-4);
  background: var(--kg-border-subtle);
}

.session-section {
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
}

.section-label {
  padding: 0 var(--kg-space-4) var(--kg-space-2);
  color: var(--kg-text-tertiary);
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
}

.session-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding: 0 var(--kg-space-2) var(--kg-space-3);
}

.session-item {
  position: relative;
  width: 100%;
  height: 34px;
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  padding: 0 var(--kg-space-3);
  border: 0;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast);
}

.session-item:hover {
  background: rgb(255 255 255 / 68%);
  color: var(--kg-text-primary);
}

.session-item.active {
  background: #fff;
  color: var(--kg-accent);
}

.session-title {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-time {
  flex: none;
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.session-empty {
  padding: var(--kg-space-4) var(--kg-space-3);
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
}

@media (max-width: 720px) {
  .sidebar {
    position: relative;
    z-index: 10;
    width: 68px;
    min-width: 68px;
    overflow: visible;
  }

  .brand {
    justify-content: center;
    padding: 0;
  }

  .brand-copy,
  .new-label,
  .nav-label {
    display: none;
  }

  .new-btn {
    width: 36px;
    justify-content: center;
    margin: var(--kg-space-3) auto var(--kg-space-2);
    padding: 0;
  }

  .nav-section { padding: 0 var(--kg-space-2); }
  .nav-item { justify-content: center; padding: 0; }

  .rail-history {
    width: 52px;
    height: 34px;
    margin: var(--kg-space-2) auto 0;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: var(--kg-radius-sm);
    background: transparent;
    color: var(--kg-text-tertiary);
    cursor: pointer;
  }

  .rail-history:hover,
  .rail-history.active { background: var(--kg-bg-surface-2); color: var(--kg-text-primary); }

  .divider { margin: var(--kg-space-3) var(--kg-space-2); }

  .session-section {
    position: absolute;
    top: calc(var(--kg-pagebar-height) + var(--kg-space-3));
    bottom: var(--kg-space-3);
    left: calc(100% + var(--kg-space-2));
    width: 240px;
    display: none;
    padding-top: var(--kg-space-3);
    overflow: hidden;
    border: 1px solid var(--kg-border-default);
    border-radius: var(--kg-radius-lg);
    background: var(--kg-bg-elevated);
    box-shadow: 0 18px 42px rgb(38 55 85 / 18%);
  }

  .session-section.open { display: flex; }
}
</style>
