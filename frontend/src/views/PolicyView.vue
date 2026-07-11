<template>
  <div class="kg-page policy-page">
    <div class="kg-page-inner policy-inner">
      <header class="page-head">
        <div>
          <p class="page-description">管理额外限制，并查看系统内置的只读基线。</p>
        </div>
        <el-button type="primary" @click="openAddDialog">
          <KgIcon name="plus" :size="15" />
          添加策略
        </el-button>
      </header>

      <section class="policy-section custom-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">自定义策略</h2>
            <p>黑名单和保护路径会收紧限制；添加只读白名单前，请确认命令没有副作用。</p>
          </div>
          <span class="section-count">{{ custom.length }} 条</span>
        </div>

        <el-table v-if="custom.length" :data="custom" class="policy-table">
          <el-table-column label="类型" width="132">
            <template #default="{ row }">
              <span class="kind-badge" :class="row.kind">{{ kindLabel(row.kind) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="模式" min-width="260">
            <template #default="{ row }"><code class="policy-pattern">{{ row.pattern }}</code></template>
          </el-table-column>
          <el-table-column label="说明" min-width="220">
            <template #default="{ row }"><span :class="{ muted: !row.note }">{{ row.note || '—' }}</span></template>
          </el-table-column>
          <el-table-column label="" width="72" align="right">
            <template #default="{ row }">
              <el-button
                text
                type="danger"
                :loading="removingId === row.id"
                @click="remove(row.id)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-else class="kg-empty policy-empty">
          <KgIcon name="shield" :size="24" />
          <strong>还没有自定义策略</strong>
          <span>需要额外限制命令或路径时，可以在这里添加。</span>
          <el-button @click="openAddDialog">添加策略</el-button>
        </div>
      </section>

      <section class="policy-section builtin-section">
        <div class="section-head">
          <div>
            <div class="builtin-heading">
              <h2 class="kg-section-title">内置策略</h2>
              <span class="readonly-mark"><KgIcon name="lock" :size="12" />只读</span>
            </div>
            <p>这些规则随系统提供，在此页面中不可编辑。</p>
          </div>
        </div>

        <el-collapse v-if="builtin" class="baseline-collapse">
          <el-collapse-item name="blacklist">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="warning" :size="15" />
                <span>危险命令黑名单</span>
                <span class="baseline-count">{{ builtin.blacklist.length }} 条</span>
              </div>
            </template>
            <div class="rule-list">
              <div v-for="([pattern, label]) in builtin.blacklist" :key="pattern" class="rule-row">
                <code>{{ pattern }}</code>
                <span>{{ label }}</span>
              </div>
            </div>
          </el-collapse-item>

          <el-collapse-item name="escalators">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="lock" :size="15" />
                <span>提权与子 Shell</span>
                <span class="baseline-count">{{ builtin.privilege_escalators.length }} 个</span>
              </div>
            </template>
            <p class="baseline-note">以下执行器会被直接拒绝。</p>
            <code class="code-line">{{ builtin.privilege_escalators.join('  ') }}</code>
          </el-collapse-item>

          <el-collapse-item name="protected">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="shield" :size="15" />
                <span>保护路径</span>
                <span class="baseline-count">{{ builtin.protected_prefixes.length }} 个</span>
              </div>
            </template>
            <p class="baseline-note">对这些路径的写操作会被拒绝。</p>
            <code class="code-line">{{ builtin.protected_prefixes.join('  ') }}</code>
          </el-collapse-item>

          <el-collapse-item name="readonly">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="terminal" :size="15" />
                <span>只读命令</span>
                <span class="baseline-count">{{ Object.keys(builtin.safe_commands).length }} 个</span>
              </div>
            </template>
            <div class="rule-list compact">
              <div v-for="(flags, command) in builtin.safe_commands" :key="command" class="rule-row">
                <code>{{ command }}</code>
                <span>{{ flags.length ? `禁用参数：${flags.join(' ')}` : '无额外参数限制' }}</span>
              </div>
            </div>
            <p class="baseline-note systemctl-note">
              systemctl 只读子命令：<code>{{ builtin.systemctl_ro_subcmds.join(' ') }}</code>
            </p>
          </el-collapse-item>

          <el-collapse-item name="shell">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="terminal" :size="15" />
                <span>Shell 元字符与 sudoers</span>
              </div>
            </template>
            <div class="baseline-copy">
              <p>元字符模式 <code>{{ builtin.metachars }}</code>：出现即拒绝。</p>
              <p>sudoers 精确白名单由部署脚本写入 <code>/etc/sudoers.d/kylinguard</code>，此处不可编辑。</p>
            </div>
          </el-collapse-item>
        </el-collapse>

        <div v-else class="baseline-loading">
          <span class="kg-spinner"></span>
          正在读取内置策略
        </div>
      </section>
    </div>

    <el-dialog v-model="addDialog" title="添加策略" width="480px" align-center>
      <el-form label-position="top" @submit.prevent="add">
        <el-form-item label="策略类型">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option value="blacklist" label="黑名单（正则）" />
            <el-option value="readonly" label="只读白名单（命令名）" />
            <el-option value="protected" label="保护路径（前缀）" />
          </el-select>
        </el-form-item>

        <el-form-item label="模式">
          <el-input v-model="form.pattern" :placeholder="patternPlaceholder" @keyup.enter="add" />
          <div class="field-help">{{ kindHelp }}</div>
        </el-form-item>

        <el-form-item label="说明（可选）">
          <el-input v-model="form.note" placeholder="说明这条策略的用途" />
        </el-form-item>

        <div v-if="error" class="form-error">
          <KgIcon name="warning" :size="14" />
          {{ error }}
        </div>
      </el-form>

      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="add">添加策略</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useAuth.js'

const custom = ref([])
const builtin = ref(null)
const error = ref('')
const addDialog = ref(false)
const saving = ref(false)
const removingId = ref(null)
const form = reactive({ kind: 'blacklist', pattern: '', note: '' })

const kindLabel = (kind) => ({
  blacklist: '黑名单', readonly: '只读白名单', protected: '保护路径',
}[kind] || kind)

const patternPlaceholder = computed(() => ({
  blacklist: '例如：\\bwipefs\\b',
  readonly: '例如：ps',
  protected: '例如：/etc/kylin-release',
}[form.kind]))

const kindHelp = computed(() => ({
  blacklist: '输入用于匹配危险命令的正则表达式。',
  readonly: '输入确认没有副作用的命令名。',
  protected: '输入禁止写入的路径前缀。',
}[form.kind]))

function openAddDialog() {
  error.value = ''
  form.pattern = ''
  form.note = ''
  addDialog.value = true
}

async function refresh() {
  const response = await apiFetch('/api/policies')
  const body = await response.json()
  custom.value = body.custom
  builtin.value = body.builtin
}

async function add() {
  if (saving.value) return
  error.value = ''
  if (!form.pattern.trim()) {
    error.value = '请输入策略模式'
    return
  }

  saving.value = true
  try {
    const response = await apiFetch('/api/policies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (!response.ok) {
      error.value = (await response.json()).detail || '添加失败'
      return
    }
    addDialog.value = false
    await refresh()
  } finally {
    saving.value = false
  }
}

async function remove(id) {
  if (removingId.value != null) return
  removingId.value = id
  try {
    await apiFetch(`/api/policies/${id}`, { method: 'DELETE' })
    await refresh()
  } finally {
    removingId.value = null
  }
}

onMounted(refresh)
</script>

<style scoped>
.policy-inner { width: min(100%, 960px); }

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-6);
}

.page-head :deep(.el-button) { gap: 7px; }

.page-description {
  margin: 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
}

.policy-section { margin-top: var(--kg-space-8); }

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-5);
  margin-bottom: var(--kg-space-3);
}

.section-head p {
  margin: 3px 0 0;
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.section-count {
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

.policy-table { width: 100%; }
.policy-pattern { color: var(--kg-text-primary); font-family: var(--kg-font-mono); font-size: 12px; }
.muted { color: var(--kg-text-disabled); }

.kind-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 7px;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-xs);
  color: var(--kg-text-secondary);
  font-size: 11px;
  line-height: 16px;
}

.kind-badge.blacklist { border-color: var(--kg-danger-border); color: var(--kg-danger); }
.kind-badge.readonly { border-color: var(--kg-success-border); color: var(--kg-success); }
.kind-badge.protected { border-color: var(--kg-warning-border); color: var(--kg-warning); }

.policy-empty {
  min-height: 190px;
  border-top: 1px solid var(--kg-border-subtle);
  border-bottom: 1px solid var(--kg-border-subtle);
}

.builtin-section { padding-bottom: var(--kg-space-6); }

.builtin-heading {
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
}

.readonly-mark {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  font-weight: 500;
}

.baseline-title {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 9px;
  padding-right: var(--kg-space-3);
  color: var(--kg-text-secondary);
}

.baseline-title :deep(.kg-icon) { color: var(--kg-text-tertiary); }
.baseline-count { margin-left: auto; color: var(--kg-text-tertiary); font-family: var(--kg-font-mono); font-size: 11px; }

.baseline-collapse :deep(.el-collapse-item__header) { padding: 0 var(--kg-space-2); }
.baseline-collapse :deep(.el-collapse-item__content) { padding: 0 var(--kg-space-2) var(--kg-space-4); }

.rule-list { display: grid; gap: 1px; }

.rule-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.4fr);
  gap: var(--kg-space-4);
  padding: 7px 8px;
  border-radius: var(--kg-radius-xs);
}

.rule-row:nth-child(odd) { background: var(--kg-bg-surface-1); }

.rule-row code,
.code-line,
.baseline-copy code,
.systemctl-note code {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.rule-row span { color: var(--kg-text-tertiary); font-size: 12px; }

.baseline-note,
.baseline-copy p {
  margin: 0 0 var(--kg-space-2);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.code-line {
  display: block;
  padding: var(--kg-space-3);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-bg-code);
  line-height: 1.65;
}

.systemctl-note { margin-top: var(--kg-space-3); }
.baseline-copy p:last-child { margin-bottom: 0; }

.baseline-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--kg-space-2);
  min-height: 120px;
  border-top: 1px solid var(--kg-border-subtle);
  color: var(--kg-text-tertiary);
  font-size: 12px;
}

.field-help {
  margin-top: 5px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  line-height: 17px;
}

.form-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: -4px;
  color: var(--kg-danger);
  font-size: 12px;
}

@media (max-width: 1080px) {
  .rule-row { grid-template-columns: minmax(150px, .9fr) minmax(190px, 1.2fr); }
}
</style>
