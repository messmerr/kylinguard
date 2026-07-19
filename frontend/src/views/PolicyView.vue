<template>
  <div class="kg-page policy-page">
    <div class="kg-page-inner policy-inner">
      <el-tabs v-model="policyTab" class="main-tabs kg-enter">
        <el-tab-pane label="权限模式" name="access">

          <section class="policy-section permission-section">
        <div class="section-head permission-heading">
          <div>
            <h2 class="kg-section-title">Agent 权限</h2>
            <p>基础模式全局生效；完全访问只绑定当前任务，不会影响其他任务。</p>
          </div>
          <span
            class="sync-state"
            :class="{ synced: permissionContext.synced, loading: permissionLoading, error: permissionLoadError }"
            :title="permissionLoadError || ''"
            role="status"
          >
            {{ permissionSyncText }}
          </span>
        </div>

        <div
          v-if="permissionLoading"
          class="policy-state"
          role="status"
          aria-live="polite"
        >
          <span class="kg-spinner" aria-hidden="true"></span>
          <div><strong>正在同步任务权限</strong><span>同步完成前不会把本地状态显示为已生效。</span></div>
        </div>

        <div
          v-else-if="permissionLoadError"
          class="policy-state is-error"
          role="alert"
        >
          <KgIcon name="warning" :size="17" />
          <div><strong>任务权限同步失败</strong><span>{{ permissionLoadError }}</span></div>
          <el-button size="small" :loading="permissionLoading" @click="retryPermissionContext">重新同步</el-button>
        </div>

        <template v-else>
        <div
          class="mode-grid"
          role="radiogroup"
          aria-label="Agent 权限模式"
          :aria-busy="permissionChanging"
        >
          <button
            v-for="(mode, modeIndex) in visiblePermissionModes"
            :key="mode.value"
            :ref="element => setPermissionModeButtonRef(mode.value, element)"
            type="button"
            class="mode-card kg-enter"
            :class="[`is-${mode.tone}`, { active: permissionMode === mode.value }]"
            :style="{ '--kg-enter-delay': `${modeIndex * 70}ms` }"
            role="radio"
            :aria-checked="permissionMode === mode.value"
            :aria-label="`${mode.label}：${mode.short}`"
            :tabindex="permissionModeTabIndex(mode.value)"
            :disabled="permissionChanging || permissionModeUnavailable(mode.value)"
            @keydown="handlePermissionModeKeydown($event, mode.value)"
            @click="choosePermissionMode(mode.value)"
          >
            <span class="mode-card-head">
              <KgIcon :name="mode.value === 'full_access' ? 'warning' : 'shield'" :size="15" />
              <strong>{{ mode.label }}</strong>
              <KgIcon v-if="permissionMode === mode.value" name="check" :size="14" class="mode-selected" />
            </span>
            <span>{{ mode.value === 'full_access' && !permissionContext.sessionId
              ? '请先在任务页发送一条消息'
              : mode.value === 'full_access' && !permissionContext.fullAccessAvailable
              ? (permissionContext.fullAccessUnavailableReason || '服务端未开放')
              : mode.short }}</span>
          </button>
        </div>

        <div class="effective-access" :class="{ danger: fullAccessActive }">
          <div class="execution-facts">
            <div>
              <span>{{ executionIdentitySourceLabel() }}</span>
              <code :title="permissionContext.executorIdentity">{{ permissionContext.executorIdentity }}</code>
            </div>
            <div>
              <span>Agent 工作目录</span>
              <code :title="permissionContext.workspaceRoot || '由服务器配置'">{{ permissionContext.workspaceRoot || '由服务器配置' }}</code>
            </div>
            <div v-if="fullAccessActive">
              <span>完整 Shell</span>
              <code>{{ permissionContext.commandShell }}</code>
            </div>
            <div v-if="fullAccessActive">
              <span>执行账户 UID</span>
              <code>{{ permissionContext.executionAccountSeparated ? '与后端不同' : '与后端相同' }}</code>
            </div>
          </div>
          <p>{{ permissionModeMeta.description }}</p>
          <el-button
            v-if="fullAccessActive"
            size="small"
            type="danger"
            plain
            :loading="permissionChanging"
            @click="choosePermissionMode('ask')"
          >收回完全访问</el-button>
        </div>
        <div v-if="permissionContext.grantsRoot" class="root-access-warning" role="alert">
          <KgIcon name="warning" :size="17" />
          <div>
            <strong>该执行身份拥有 root 权限</strong>
            <span>开启完全访问后，Agent 可在不逐项确认的情况下以 root 执行完整 Shell、文件、网络和进程操作。</span>
          </div>
        </div>
        </template>
          </section>
        </el-tab-pane>

        <el-tab-pane label="授权管理" name="grants">

          <section class="policy-section grants-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">全局自动执行范围与会话授权</h2>
            <p>这些服务器目录对所有任务生效；自动审核只放行审核通过的可逆操作，高风险和破坏性动作仍会询问。</p>
          </div>
          <span class="section-count">{{ autoReviewRoots.length }} 个目录 · {{ activePermissionGrants.length }} 条授权</span>
        </div>

        <div v-if="permissionLoading" class="policy-state" role="status">
          <span class="kg-spinner" aria-hidden="true"></span>
          <div><strong>正在同步授权范围</strong><span>自动执行范围与操作授权同步完成后再显示。</span></div>
        </div>

        <div v-else-if="permissionLoadError" class="policy-state is-error" role="alert">
          <KgIcon name="warning" :size="17" />
          <div><strong>授权范围同步失败</strong><span>{{ permissionLoadError }}</span></div>
          <el-button size="small" :loading="permissionLoading" @click="retryPermissionContext">重新同步</el-button>
        </div>

        <template v-else>
        <div class="root-entry">
          <el-input
            v-model="autoReviewRootInput"
            placeholder="服务器绝对路径，例如 /srv/project/docs"
            :disabled="grantSaving"
            @keyup.enter="addRoot"
          />
          <el-button :loading="grantSaving" @click="addRoot">添加范围</el-button>
        </div>
        <p class="server-path-note">
          <KgIcon name="server" :size="13" />
          浏览器本地文件夹不会出现在这里；请输入 Agent 所在服务器上的路径。
        </p>

        <div v-if="autoReviewRoots.length" class="auto-review-root-list">
          <article v-for="path in autoReviewRoots" :key="path" class="grant-row auto-review-root-row">
            <span class="grant-icon"><KgIcon name="disk" :size="15" /></span>
            <div class="grant-copy">
              <code :title="path">{{ path }}</code>
              <span>{{ permissionMode === 'auto_review' ? '全局自动审核已启用' : '切换到自动审核后生效' }} · 包含子目录</span>
            </div>
            <span class="grant-lifetime">全局</span>
            <el-button
              text
              type="danger"
              :loading="removingRootPath === path"
              :disabled="permissionMode === 'auto_review' && path === permissionContext.defaultWorkspaceRoot"
              @click="removeRoot(path)"
            >移除目录</el-button>
          </article>
        </div>

        <div class="grant-subhead">
          <strong>操作授权</strong>
          <span>“允许一次”与“本次会话允许”只绑定具体动作，不会扩大自动执行范围。</span>
        </div>

        <div v-if="activePermissionGrants.length" class="grant-list">
          <article v-for="grant in activePermissionGrants" :key="grant.id" class="grant-row">
            <span class="grant-icon"><KgIcon :name="grant.resourceKind === 'path' ? 'disk' : 'terminal'" :size="15" /></span>
            <div class="grant-copy">
              <code :title="grant.path || grant.label || grant.resourceKind">{{ grant.path || grant.label || grant.resourceKind }}</code>
              <span>{{ grantDescription(grant) }}</span>
            </div>
            <span class="grant-lifetime">{{ lifetimeLabel(grant.lifetime) }}</span>
            <el-button
              text
              type="danger"
              :loading="removingGrantId === grant.id"
              @click="removeGrant(grant)"
            >收回</el-button>
          </article>
        </div>
        <div v-else class="grant-empty">
          <KgIcon name="lock" :size="17" />
          <span>还没有操作授权。普通修改会按当前模式询问。</span>
        </div>
        </template>
          </section>
        </el-tab-pane>

        <el-tab-pane label="策略规则" name="rules">

          <div v-if="policyLoading" class="policy-state policy-page-state" role="status">
            <span class="kg-spinner" aria-hidden="true"></span>
            <div><strong>正在读取安全策略</strong><span>正在同步自定义规则与只读基线。</span></div>
          </div>

          <div v-else-if="policyLoadError" class="policy-state policy-page-state is-error" role="alert">
            <KgIcon name="warning" :size="18" />
            <div><strong>安全策略暂时未加载</strong><span>{{ policyLoadError }}</span></div>
            <el-button size="small" :loading="policyLoading" @click="refresh">重新加载</el-button>
          </div>

          <template v-else>
          <section class="policy-section custom-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">自定义策略</h2>
            <p>命令正则和保护路径会提升确认强度；完全访问可覆盖这些风险策略。</p>
          </div>
          <div class="section-meta">
            <span class="section-count">{{ custom.length }} 条</span>
            <el-button
              type="primary"
              aria-label="添加自定义安全策略"
              @click="openAddDialog"
            >
              <KgIcon name="plus" :size="15" />
              添加策略
            </el-button>
          </div>
        </div>

        <el-table v-if="custom.length" :data="custom" class="policy-table">
          <el-table-column label="类型" width="132">
            <template #default="{ row }">
              <span class="kind-badge" :class="row.kind">{{ kindLabel(row.kind) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="模式" min-width="260">
            <template #default="{ row }"><code class="policy-pattern" :title="row.pattern">{{ row.pattern }}</code></template>
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
          <span>需要让某类命令或路径获得更醒目的风险确认时，可以在这里添加。</span>
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
            <p>这些规则用于风险分类与权限决策，不是 Agent 能力清单。</p>
          </div>
        </div>

        <el-collapse v-if="builtin" class="baseline-collapse">
          <el-collapse-item name="blacklist">
            <template #title>
              <div class="baseline-title">
                <KgIcon name="warning" :size="15" />
                <span>高风险命令模式</span>
                <span class="baseline-count">{{ builtin.blacklist.length }} 条</span>
              </div>
            </template>
            <p class="baseline-note">命中后标记为高风险并要求显式授权；完全访问模式下不会仅因类别而禁用能力。</p>
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
            <p class="baseline-note">以下执行器会提升风险并进入权限判断，不会仅因提权或启动子 Shell 而直接拒绝。</p>
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
            <p class="baseline-note">普通模式会拦截或复核显式控制面路径；明确开启的完全访问可覆盖产品层路径限制。不同 UID 只说明执行账户分离，是否真正隔离仍取决于文件权限与 ACL。</p>
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
                <span>完整 Shell 语法与执行身份</span>
              </div>
            </template>
            <div class="baseline-copy">
              <p>元字符模式 <code>{{ builtin.metachars }}</code> 是完整 Shell 风险信号，会进入风险与权限判断，不代表出现即拒绝。</p>
              <p>配置专用执行账户时，sudoers 可由部署脚本写入 <code>/etc/sudoers.d/kylinguard</code>；留空则使用页面显示的后端当前 OS 身份。</p>
            </div>
          </el-collapse-item>
        </el-collapse>

        <div v-else class="baseline-loading is-error" role="alert">
          <KgIcon name="warning" :size="16" />
          内置策略数据不完整
          <el-button size="small" @click="refresh">重新加载</el-button>
        </div>
          </section>
          </template>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog
      v-model="addDialog"
      title="添加策略"
      width="min(480px, calc(100vw - 28px))"
      align-center
      :close-on-click-modal="!saving"
      :show-close="!saving"
    >
      <el-form label-position="top" @submit.prevent="add">
        <el-form-item label="策略类型">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option value="blacklist" label="命令风险规则（正则）" />
              <el-option value="readonly" label="可信命令（仍需复核）" />
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
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useApi.js'
import { activeId, setChatPermissionMode } from '../composables/useChat.js'
import { confirmFullAccessEnable } from '../utils/fullAccessWarnings.js'
import {
  addAutoReviewRoot,
  autoReviewRoots,
  fullAccessActive,
  executionIdentitySourceLabel,
  loadPermissionContext,
  permissionContext,
  permissionLoadError,
  permissionGrants,
  permissionLoading,
  permissionMode,
  permissionModeMeta,
  revokePermissionGrant,
  revokeAutoReviewRoot,
  visiblePermissionModes,
} from '../composables/usePermissions.js'

const custom = ref([])
const builtin = ref(null)
const policyLoading = ref(true)
const policyLoadError = ref('')
const error = ref('')
const addDialog = ref(false)
const saving = ref(false)
const removingId = ref(null)
const form = reactive({ kind: 'blacklist', pattern: '', note: '' })
const permissionChanging = ref(false)
const grantSaving = ref(false)
const removingGrantId = ref('')
const removingRootPath = ref('')
const autoReviewRootInput = ref('')
const policyTab = ref('access')
const permissionModeButtonRefs = new Map()
let policyRequest = 0

const permissionSyncText = computed(() => {
  if (permissionLoading.value) return '正在同步全局权限…'
  if (permissionLoadError.value) return '权限同步失败'
  if (permissionContext.synced) return '已与服务器同步'
  return '全局权限尚未同步'
})

const activePermissionGrants = computed(() => permissionGrants.value.filter(
  (grant) => !grant.revoked,
))

const kindLabel = (kind) => ({
  blacklist: '命令风险', readonly: '可信命令', protected: '保护路径',
}[kind] || kind)

const patternPlaceholder = computed(() => ({
  blacklist: '例如：\\bwipefs\\b',
  readonly: '例如：ps',
  protected: '例如：/etc/kylin-release',
}[form.kind]))

const kindHelp = computed(() => ({
  blacklist: '匹配后要求显式权限；它不是完整 Shell 的不可绕过沙箱。',
  readonly: '允许该命令进入后续权限和风险复核，不会自动执行。',
  protected: '结构化文件写入或命令路径命中后升级为高风险确认；完全访问可覆盖。',
}[form.kind]))

const ACTION_LABELS = {
  read: '读取', create: '创建', write: '写入', modify: '修改',
  delete: '删除', execute: '执行', control: '控制服务', elevate: '提权',
}

function grantDescription(grant) {
  const actions = (grant.actions || []).map((action) => ACTION_LABELS[action] || action)
  return actions.length ? `允许${actions.join('、')}${grant.recursive ? ' · 包含子目录' : ''}` : '授权范围由服务器决定'
}

function lifetimeLabel(lifetime) {
  return {
    once: '仅一次', session: '本次会话', extended: '12 小时',
  }[lifetime] || lifetime || '本次会话'
}

function permissionModeUnavailable(mode) {
  return mode === 'full_access'
    && (!permissionContext.sessionId || !permissionContext.fullAccessAvailable)
}

function navigablePermissionModes() {
  return visiblePermissionModes.value.filter(mode => !permissionModeUnavailable(mode.value))
}

function permissionModeTabIndex(mode) {
  const modes = navigablePermissionModes()
  const rovingMode = modes.some(item => item.value === permissionMode.value)
    ? permissionMode.value
    : modes[0]?.value
  return mode === rovingMode ? 0 : -1
}

function setPermissionModeButtonRef(mode, element) {
  if (element) permissionModeButtonRefs.set(mode, element)
  else permissionModeButtonRefs.delete(mode)
}

async function handlePermissionModeKeydown(event, currentMode) {
  if (permissionChanging.value) return
  const modes = navigablePermissionModes()
  if (!modes.length) return

  const currentIndex = Math.max(0, modes.findIndex(mode => mode.value === currentMode))
  let targetIndex
  switch (event.key) {
    case 'ArrowRight':
    case 'ArrowDown':
      targetIndex = (currentIndex + 1) % modes.length
      break
    case 'ArrowLeft':
    case 'ArrowUp':
      targetIndex = (currentIndex - 1 + modes.length) % modes.length
      break
    case 'Home':
      targetIndex = 0
      break
    case 'End':
      targetIndex = modes.length - 1
      break
    default:
      return
  }

  event.preventDefault()
  const targetMode = modes[targetIndex].value
  permissionModeButtonRefs.get(targetMode)?.focus()
  await choosePermissionMode(targetMode)
  await nextTick()
  const selectedMode = navigablePermissionModes().some(mode => mode.value === permissionMode.value)
    ? permissionMode.value
    : targetMode
  permissionModeButtonRefs.get(selectedMode)?.focus()
}

async function choosePermissionMode(mode) {
  if (permissionChanging.value || mode === permissionMode.value) return
  if (mode === 'full_access' && !permissionContext.fullAccessAvailable) {
    ElMessage.info(permissionContext.fullAccessUnavailableReason || '服务端未开放完全访问')
    return
  }
  permissionChanging.value = true
  try {
    if (mode === 'full_access') {
      await confirmFullAccessEnable({
        executorIdentity: permissionContext.executorIdentity,
        grantsRoot: permissionContext.grantsRoot,
      })
    }
    const result = await setChatPermissionMode(mode, mode === 'auto_review'
        ? { autoReviewRoots: autoReviewRoots.value }
        : {})
    if (!result.supported) {
      ElMessage.warning('当前后端未保存全局权限设置')
    } else {
      ElMessage.success(mode === 'full_access' ? '全局完整执行能力已开启' : '全局权限已更新')
    }
  } catch (reason) {
    if (reason === 'cancel' || reason === 'close' || reason?.action === 'cancel') return
    ElMessage.error(reason.message || '权限修改失败')
  } finally {
    permissionChanging.value = false
  }
}

async function retryPermissionContext() {
  if (permissionLoading.value) return
  try {
    await loadPermissionContext(activeId.value)
  } catch {
    // usePermissions 会保留可展示的错误文本。
  }
}

async function addRoot() {
  if (grantSaving.value) return
  const path = autoReviewRootInput.value.trim()
  if (!path) {
    ElMessage.warning('请输入服务器目录')
    return
  }
  grantSaving.value = true
  try {
    const result = await addAutoReviewRoot(path)
    if (result.supported) {
      autoReviewRootInput.value = ''
      ElMessage.success('自动执行范围已添加')
    } else {
      ElMessage.warning('当前后端未保存全局自动执行范围')
    }
  } catch (reason) {
    ElMessage.error(reason.message || '自动执行范围添加失败')
  } finally {
    grantSaving.value = false
  }
}

async function removeGrant(grant) {
  if (removingGrantId.value) return
  removingGrantId.value = grant.id
  try {
    await revokePermissionGrant(grant)
    ElMessage.success('授权已收回，后续操作会再次询问')
  } catch (reason) {
    ElMessage.error(reason.message || '授权收回失败')
  } finally {
    removingGrantId.value = ''
  }
}

async function removeRoot(path) {
  if (removingRootPath.value) return
  removingRootPath.value = path
  try {
    await revokeAutoReviewRoot(path)
    ElMessage.success('自动执行范围已移除')
  } catch (reason) {
    ElMessage.error(reason.message || '自动执行范围移除失败')
  } finally {
    removingRootPath.value = ''
  }
}

function openAddDialog() {
  error.value = ''
  form.pattern = ''
  form.note = ''
  addDialog.value = true
}

async function refresh() {
  const requestId = ++policyRequest
  policyLoading.value = true
  policyLoadError.value = ''
  try {
    const response = await apiFetch('/api/policies')
    if (!response.ok) throw new Error(await responseError(response, '服务器未能返回安全策略'))
    const body = await response.json()
    if (!Array.isArray(body.custom) || !body.builtin) {
      throw new Error('服务器返回的安全策略数据不完整')
    }
    if (requestId !== policyRequest) return
    custom.value = body.custom
    builtin.value = body.builtin
  } catch (reason) {
    if (requestId === policyRequest) {
      policyLoadError.value = reason.message || '请检查后端服务后重试'
    }
  } finally {
    if (requestId === policyRequest) policyLoading.value = false
  }
}

async function responseError(response, fallback) {
  try {
    const body = await response.clone().json()
    return body.detail || body.message || fallback
  } catch {
    return fallback
  }
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
    if (!response.ok) throw new Error(await responseError(response, '添加失败'))
    addDialog.value = false
    await refresh()
    if (policyLoadError.value) ElMessage.warning('策略已添加，但列表刷新未完成')
  } catch (reason) {
    error.value = reason.message || '添加失败'
  } finally {
    saving.value = false
  }
}

async function remove(id) {
  if (removingId.value != null) return
  removingId.value = id
  try {
    const response = await apiFetch(`/api/policies/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response, '删除失败'))
    await refresh()
    if (policyLoadError.value) ElMessage.warning('策略已删除，但列表刷新未完成')
  } catch (reason) {
    ElMessage.error(reason.message || '策略删除失败')
  } finally {
    removingId.value = null
  }
}

onMounted(() => {
  refresh()
  loadPermissionContext(activeId.value).catch(() => {})
})
</script>

<style scoped>
.policy-inner { width: 100%; }

.main-tabs { margin-top: 0; }
.main-tabs :deep(.el-tabs__header) { margin-bottom: var(--kg-space-4); }
.main-tabs :deep(.el-tabs__content) { overflow: visible; }

.policy-section { margin-top: var(--kg-space-4); }

.permission-section,
.grants-section {
  padding-bottom: var(--kg-space-2);
  border-bottom: 1px solid var(--kg-border-subtle);
}

.permission-heading { align-items: flex-end; }
.sync-state {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 7px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-pill);
  color: var(--kg-text-tertiary);
  font-size: 10px;
}
.sync-state.synced { border-color: var(--kg-success-border); color: var(--kg-success); }
.sync-state.loading { border-color: var(--kg-info-border); color: var(--kg-info); }
.sync-state.error { border-color: var(--kg-danger-border); color: var(--kg-danger); }

.mode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--kg-space-3);
}

.mode-card {
  min-width: 0;
  min-height: 82px;
  display: grid;
  align-content: start;
  gap: 9px;
  padding: 14px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: var(--kg-shadow-xs);
  color: var(--kg-text-tertiary);
  text-align: left;
  cursor: pointer;
  transition: border-color var(--kg-motion-fast), background var(--kg-motion-fast),
    color var(--kg-motion-fast), box-shadow var(--kg-motion-fast),
    transform var(--kg-motion-fast);
}

/* 入场动画只回填第一帧，播完后交还 transform，hover 位移才能生效 */
.mode-card.kg-enter { animation-fill-mode: backwards; }

.mode-card:hover:not(:disabled):not(.active) {
  border-color: var(--kg-border-default);
  box-shadow: var(--kg-shadow-md);
  transform: translateY(-2px);
}
/* 选中态：inset 叠出第二圈 accent 描边（2px 观感），避免 1px→2px 的布局抖动 */
.mode-card.active {
  border-color: var(--kg-accent);
  background: var(--kg-accent-soft);
  box-shadow: inset 0 0 0 1px var(--kg-accent), var(--kg-shadow-sm);
}
.mode-card:disabled { opacity: .55; cursor: not-allowed; }

.mode-card-head { display: flex; align-items: center; gap: 7px; }
.mode-card-head strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 600; }
.mode-card > span:last-child { font-size: 12px; line-height: 1.5; }
.mode-selected { margin-left: auto; color: var(--kg-accent); }
.mode-card.is-danger .mode-card-head :deep(.kg-icon) { color: var(--kg-danger); }
.mode-card.is-danger .mode-card-head :deep(.mode-selected) { color: var(--kg-accent); }

.effective-access {
  min-height: 52px;
  display: flex;
  align-items: center;
  gap: var(--kg-space-6);
  margin-top: var(--kg-space-3);
  padding: var(--kg-space-3) var(--kg-space-4);
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-2);
}
.effective-access.danger { border-color: var(--kg-danger-border); background: var(--kg-danger-soft); }
.execution-facts { display: flex; flex: 0 1 auto; min-width: 0; gap: var(--kg-space-6); }
.execution-facts > div { display: grid; min-width: 0; gap: 2px; }
/* 信息条 label 采用 eyebrow 风格 */
.execution-facts span {
  color: var(--kg-text-tertiary);
  font-size: 11px;
  font-weight: 550;
  letter-spacing: .07em;
  text-transform: uppercase;
}
.effective-access code {
  overflow: hidden;
  max-width: 100%;
  color: var(--kg-text-primary);
  font: 13px/1.5 var(--kg-font-mono);
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.effective-access p { min-width: 0; flex: 1; margin: 0; color: var(--kg-text-secondary); font-size: 11px; line-height: 1.5; text-align: right; }
.root-access-warning {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: var(--kg-space-3);
  padding: var(--kg-space-3) var(--kg-space-4);
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
}
.root-access-warning > :deep(.kg-icon) { flex: none; margin-top: 1px; }
.root-access-warning > div { display: grid; min-width: 0; gap: 2px; }
.root-access-warning strong { font-size: 12px; font-weight: 650; }
.root-access-warning span { color: var(--kg-text-secondary); font-size: 11px; line-height: 1.6; }

.root-entry { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--kg-space-2); }
.server-path-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 7px 0 0;
  color: var(--kg-text-tertiary);
  font-size: 10px;
}

.grant-list { display: grid; gap: 1px; margin-top: var(--kg-space-3); }
.auto-review-root-list { display: grid; gap: 1px; margin-top: var(--kg-space-3); }
.auto-review-root-row { border-color: var(--kg-success-border); }
.grant-subhead {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin-top: var(--kg-space-5);
  color: var(--kg-text-tertiary);
  font-size: 10px;
}
.grant-subhead strong { color: var(--kg-text-secondary); font-size: 11px; font-weight: 600; }
.grant-row {
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px var(--kg-space-3);
  border-top: 1px solid var(--kg-border-subtle);
}
.grant-row:last-child { border-bottom: 1px solid var(--kg-border-subtle); }
.grant-icon {
  width: 30px;
  height: 30px;
  display: grid;
  flex: none;
  place-items: center;
  border-radius: var(--kg-radius-sm);
  background: var(--kg-bg-surface-2);
  color: var(--kg-accent);
}
.grant-copy { min-width: 0; flex: 1; display: grid; gap: 3px; }
.grant-copy code { overflow: hidden; color: var(--kg-text-primary); font: 11px/1.4 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.grant-copy span { color: var(--kg-text-tertiary); font-size: 10px; }
.grant-lifetime {
  width: 72px;
  flex: none;
  color: var(--kg-text-tertiary);
  font-size: 10px;
  text-align: center;
}
.grant-row > :deep(.el-button) {
  width: 84px;
  flex: none;
  justify-content: center;
}
.grant-empty {
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: var(--kg-space-3);
  border-top: 1px solid var(--kg-border-subtle);
  border-bottom: 1px solid var(--kg-border-subtle);
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.section-head {
  display: flex;
  align-items: flex-end;
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

.section-meta {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.section-meta :deep(.el-button) { gap: 7px; }

.policy-table { width: 100%; }
.policy-pattern {
  color: var(--kg-text-primary);
  font-family: var(--kg-font-mono);
  font-size: 12px;
  overflow-wrap: anywhere;
}
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
  padding: 9px var(--kg-space-3);
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
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-secondary);
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
  .mode-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .effective-access { align-items: flex-start; flex-wrap: wrap; }
  .effective-access p { width: 100%; text-align: left; }
  .execution-facts { width: 100%; flex-wrap: wrap; }
  .root-entry { grid-template-columns: minmax(0, 1fr) auto; }
  .root-entry > :deep(.el-button) { grid-column: 1 / -1; justify-self: end; }
  .rule-row { grid-template-columns: minmax(150px, .9fr) minmax(190px, 1.2fr); }
}
</style>
