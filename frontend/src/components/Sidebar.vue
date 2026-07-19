<template>
  <aside ref="sidebar" class="sidebar" aria-label="主导航" @keydown.esc="historyOpen = false">
    <div class="brand">
      <span class="brand-mark"><KgLogo :size="22" /></span>
      <span class="brand-copy">
        <strong>麒盾</strong>
        <span>KylinGuard</span>
      </span>
    </div>

    <button class="new-btn" type="button" title="新建任务" @click="startTask">
      <KgIcon name="plus" :size="16" />
      <span class="new-label">新建任务</span>
    </button>

    <nav class="nav-section" aria-label="工作区">
      <button v-for="v in VIEWS" :key="v.key" class="nav-item"
              :class="{ active: view === v.key }" type="button"
              :title="navTitle(v)" :aria-label="navTitle(v)"
              :aria-current="view === v.key ? 'page' : undefined"
              @click="navigate(v.key)">
        <KgIcon :name="v.icon" :size="16" />
        <span class="nav-label">{{ v.label }}</span>
        <span
          v-if="v.key === 'alerts' && pendingAlertBadge"
          class="nav-badge"
          aria-hidden="true"
        >{{ pendingAlertBadge }}</span>
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
                type="button" :title="s.title"
                :aria-current="view === 'chat' && s.id === activeId ? 'page' : undefined"
                @click="openSession(s.id)">
          <span class="session-title">{{ s.title }}</span>
          <time class="session-time">{{ timeText(s.updated_at) }}</time>
        </button>
        <div v-if="!sessions.length" class="session-empty">还没有任务记录</div>
      </div>
    </section>

  </aside>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { pendingAlertCount } from '../composables/useAlerts.js'
import { activeId, loadSession, newSession, sessions } from '../composables/useChat.js'
import { alertBadgeText } from '../utils/alerts.js'
import { formatRelativeTime } from '../utils/relativeTime.js'
import KgIcon from './KgIcon.vue'
import KgLogo from './KgLogo.vue'

defineProps({ view: { type: String, required: true } })
const emit = defineEmits(['change-view'])
const historyOpen = ref(false)
const sidebar = ref(null)
const relativeClock = ref(Date.now())
let relativeClockTimer = null
const pendingAlertBadge = computed(() => alertBadgeText(pendingAlertCount.value))

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

function navTitle(item) {
  if (item.key !== 'alerts' || !pendingAlertCount.value) return item.label
  return `${item.label}，${pendingAlertCount.value} 条待处理告警`
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
  width: 32px;
  height: 32px;
  display: grid;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 9px;
  background: var(--kg-accent-gradient);
  color: #fff;
  box-shadow: 0 6px 16px rgb(23 92 255 / 30%),
    inset 0 1px 0 rgb(255 255 255 / 22%);
}

.brand-copy {
  min-width: 0;
  display: grid;
  line-height: 1.15;
}

.brand-copy strong {
  background: var(--kg-accent-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-size: 16px;
  font-weight: 750;
  letter-spacing: .02em;
}

.brand-copy span {
  margin-top: 3px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  letter-spacing: .04em;
}

.new-btn {
  height: 36px;
  margin: var(--kg-space-3) var(--kg-space-3) var(--kg-space-2);
  padding: 0 var(--kg-space-3);
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  flex: none;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-md);
  background: var(--kg-accent-gradient);
  color: #fff;
  font-size: 13px;
  font-weight: 550;
  cursor: pointer;
  box-shadow: var(--kg-shadow-accent), inset 0 1px 0 rgb(255 255 255 / 18%);
  transition: box-shadow var(--kg-motion-fast), transform var(--kg-motion-fast),
    filter var(--kg-motion-fast);
}

.new-btn:hover:not(:disabled) {
  filter: brightness(1.07);
  box-shadow: 0 8px 20px rgb(23 92 255 / 32%),
    inset 0 1px 0 rgb(255 255 255 / 18%);
  transform: translateY(-1px);
}

.new-btn:active:not(:disabled) {
  transform: translateY(0);
  filter: brightness(.97);
}
.new-btn:disabled {
  border-color: var(--kg-border-subtle);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-disabled);
  box-shadow: none;
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
  height: 38px;
  padding: 0 var(--kg-space-3);
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: var(--kg-radius-md);
  background: transparent;
  border: 1px solid transparent;
  color: var(--kg-text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: color var(--kg-motion-fast), background var(--kg-motion-fast),
    border-color var(--kg-motion-fast), box-shadow var(--kg-motion-fast);
}

.nav-item::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 50%;
  width: 3px;
  height: 0;
  border-radius: 2px;
  background: var(--kg-accent);
  transform: translateY(-50%);
  transition: height var(--kg-motion-base) var(--kg-ease-emphasized);
}

.nav-item:hover {
  background: rgb(255 255 255 / 72%);
  color: var(--kg-text-primary);
}

/* 悬停时图标轻微前送，强化指向感 */
.nav-item :deep(.kg-icon) {
  transition: transform var(--kg-motion-base) var(--kg-ease-spring);
}

.nav-item:hover :deep(.kg-icon) { transform: translateX(2px); }

.nav-item.active {
  border-color: rgb(23 92 255 / 16%);
  background: #fff;
  color: var(--kg-accent);
  font-weight: 550;
  box-shadow: 0 4px 14px rgb(23 92 255 / 10%);
}

.nav-item.active::before { height: 18px; }

.nav-item.active :deep(.kg-icon) { color: var(--kg-accent); }

.nav-badge {
  min-width: 18px;
  height: 18px;
  margin-left: auto;
  padding: 0 5px;
  display: inline-grid;
  flex: none;
  place-items: center;
  border: 2px solid var(--kg-bg-sidebar);
  border-radius: var(--kg-radius-pill);
  background: var(--kg-danger-solid);
  color: #fff;
  font-family: var(--kg-font-mono);
  font-size: 10px;
  font-weight: 700;
  line-height: 14px;
  font-variant-numeric: tabular-nums;
  box-sizing: border-box;
}

.nav-item.active .nav-badge { border-color: #fff; }

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
  font-size: 11px;
  font-weight: 550;
  line-height: 18px;
  letter-spacing: .07em;
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
  .nav-badge {
    position: absolute;
    top: 1px;
    right: 4px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-width: 2px;
    font-size: 9px;
    line-height: 12px;
  }

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
