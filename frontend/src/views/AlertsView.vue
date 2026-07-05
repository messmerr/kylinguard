<template>
  <div class="alerts-page">
    <div class="alerts-inner">
      <h2 class="page-title">告警配置</h2>

      <el-tabs v-model="tab" class="main-tabs">
        <!-- ===== 规则 ===== -->
        <el-tab-pane label="评估规则" name="rules">
          <div class="toolbar">
            <el-button type="primary" size="small" @click="openRuleDialog()">+ 新建规则</el-button>
          </div>
          <el-table :data="rules" size="small" class="tbl">
            <el-table-column label="规则名称" prop="name" min-width="140" />
            <el-table-column label="指标" width="140">
              <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
            </el-table-column>
            <el-table-column label="条件" width="120">
              <template #default="{ row }">{{ row.operator }} {{ row.threshold }}{{ row.metric !== 'failed_services' ? '%' : '' }}</template>
            </el-table-column>
            <el-table-column label="严重度" width="100">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">{{ severityLabel(row.severity) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="沉默期" width="90">
              <template #default="{ row }">{{ row.silence_minutes }} 分钟</template>
            </el-table-column>
            <el-table-column label="推送渠道" min-width="160">
              <template #default="{ row }">
                <span v-if="!row.channel_ids.length" class="dim">（未绑定）</span>
                <el-tag v-for="cid in row.channel_ids" :key="cid" size="small" class="ch-tag">
                  {{ channelName(cid) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="70">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" size="small"
                           @change="toggleRule(row)" />
              </template>
            </el-table-column>
            <el-table-column width="100">
              <template #default="{ row }">
                <el-button size="small" text @click="openRuleDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteRule(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!rules.length" class="empty">暂无告警规则</div>
        </el-tab-pane>

        <!-- ===== 渠道 ===== -->
        <el-tab-pane label="推送渠道" name="channels">
          <div class="toolbar">
            <el-button type="primary" size="small" @click="openChDialog()">+ 新建渠道</el-button>
          </div>
          <el-table :data="channels" size="small" class="tbl">
            <el-table-column label="渠道名称" prop="name" min-width="140" />
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.type === 'webhook' ? 'info' : 'success'">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="目标" min-width="200">
              <template #default="{ row }">
                <span class="dim small">{{ chTarget(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="70">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" size="small"
                           @change="toggleChannel(row)" />
              </template>
            </el-table-column>
            <el-table-column width="160">
              <template #default="{ row }">
                <el-button size="small" text @click="testChannel(row)">测试</el-button>
                <el-button size="small" text @click="openChDialog(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteChannel(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!channels.length" class="empty">暂无推送渠道</div>
        </el-tab-pane>

        <!-- ===== 历史 ===== -->
        <el-tab-pane label="告警历史" name="history">
          <div class="toolbar">
            <el-button size="small" type="danger" plain @click="clearHistory">清空历史</el-button>
          </div>
          <el-table :data="history" size="small" class="tbl">
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ fmtTime(row.fired_at) }}</template>
            </el-table-column>
            <el-table-column label="规则" prop="rule_name" min-width="140" />
            <el-table-column label="指标" width="120">
              <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
            </el-table-column>
            <el-table-column label="值" width="80" prop="metric_value" />
            <el-table-column label="严重度" width="90">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">
                  {{ severityLabel(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="已推送渠道" min-width="160">
              <template #default="{ row }">
                <span v-if="!row.channels_notified.length" class="dim">（无）</span>
                <el-tag v-for="ch in row.channels_notified" :key="ch" size="small" class="ch-tag">{{ ch }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="说明" prop="message" min-width="200">
              <template #default="{ row }"><span class="dim small">{{ row.message }}</span></template>
            </el-table-column>
          </el-table>
          <div v-if="!history.length" class="empty">暂无告警历史</div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- ===== 规则对话框 ===== -->
    <el-dialog v-model="ruleDialog" :title="ruleForm.id ? '编辑规则' : '新建规则'"
               width="520px" align-center>
      <el-form :model="ruleForm" label-width="90px" size="small">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" placeholder="如：内存告警" />
        </el-form-item>
        <el-form-item label="监控指标">
          <el-select v-model="ruleForm.metric" style="width:100%">
            <el-option v-for="m in METRICS" :key="m.value" :value="m.value" :label="m.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发条件">
          <div style="display:flex;gap:8px;width:100%">
            <el-select v-model="ruleForm.operator" style="width:100px">
              <el-option value=">=" label=">=" />
              <el-option value=">" label=">" />
              <el-option value="<=" label="<=" />
              <el-option value="<" label="<" />
            </el-select>
            <el-input-number v-model="ruleForm.threshold" :min="0" :max="100"
                             :disabled="ruleForm.metric === 'failed_services'"
                             style="flex:1" />
            <span style="line-height:28px;color:#8b949e;font-size:12px">
              {{ ruleForm.metric !== 'failed_services' ? '%' : '（不适用）' }}
            </span>
          </div>
        </el-form-item>
        <el-form-item label="严重度">
          <el-radio-group v-model="ruleForm.severity">
            <el-radio value="warning">警告</el-radio>
            <el-radio value="critical">严重</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="沉默期">
          <el-input-number v-model="ruleForm.silence_minutes" :min="1" :max="1440" />
          <span style="margin-left:8px;color:#8b949e;font-size:12px">分钟（同规则重复触发冷却）</span>
        </el-form-item>
        <el-form-item label="推送渠道">
          <el-select v-model="ruleForm.channel_ids" multiple style="width:100%"
                     placeholder="不选则仅记录，不推送">
            <el-option v-for="ch in channels" :key="ch.id" :value="ch.id" :label="ch.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>

    <!-- ===== 渠道对话框 ===== -->
    <el-dialog v-model="chDialog" :title="chForm.id ? '编辑渠道' : '新建渠道'"
               width="540px" align-center>
      <el-form :model="chForm" label-width="100px" size="small">
        <el-form-item label="渠道名称">
          <el-input v-model="chForm.name" placeholder="如：运维群 Webhook" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="chForm.type">
            <el-radio value="webhook">Webhook</el-radio>
            <el-radio value="email">邮件</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- Webhook 配置 -->
        <template v-if="chForm.type === 'webhook'">
          <el-form-item label="URL">
            <el-input v-model="chForm.config.url" placeholder="https://..." />
          </el-form-item>
          <el-form-item label="HTTP 方法">
            <el-select v-model="chForm.config.method" style="width:120px">
              <el-option value="POST" label="POST" />
              <el-option value="PUT" label="PUT" />
            </el-select>
          </el-form-item>
          <el-form-item label="自定义 Header">
            <el-input v-model="chForm.headersRaw" type="textarea" :rows="3"
                      placeholder='{"Authorization": "Bearer xxx"}' />
          </el-form-item>
        </template>

        <!-- Email 配置 -->
        <template v-if="chForm.type === 'email'">
          <el-form-item label="SMTP 主机">
            <el-input v-model="chForm.config.host" placeholder="smtp.qq.com" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="chForm.config.port" :min="1" :max="65535" style="width:120px" />
            <el-switch v-model="chForm.config.use_tls" active-text="SSL/TLS" style="margin-left:12px" />
          </el-form-item>
          <el-form-item label="发件人账号">
            <el-input v-model="chForm.config.user" placeholder="noreply@example.com" />
          </el-form-item>
          <el-form-item label="授权码/密码">
            <el-input v-model="chForm.config.password" type="password" show-password />
          </el-form-item>
          <el-form-item label="收件人">
            <el-input v-model="chForm.config.to" placeholder="ops@example.com" />
          </el-form-item>
        </template>

        <el-form-item label="启用">
          <el-switch v-model="chForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="chDialog = false">取消</el-button>
        <el-button type="primary" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../composables/useAuth.js'

const tab = ref('rules')
const rules = ref([])
const channels = ref([])
const history = ref([])

const METRICS = [
  { value: 'memory_pct',     label: '内存使用率 (%)' },
  { value: 'cpu_pct',        label: 'CPU 使用率 (%)' },
  { value: 'disk_pct',       label: '磁盘使用率 (%)（任意盘）' },
  { value: 'failed_services',label: '存在停止的自动启动服务' },
]

const metricLabel = (v) => METRICS.find(m => m.value === v)?.label ?? v
const severityLabel = (v) => ({ warning: '警告', critical: '严重' }[v] ?? v)
const channelName = (id) => channels.value.find(c => c.id === id)?.name ?? `#${id}`
const chTarget = (ch) => ch.type === 'webhook'
  ? ch.config.url || '—'
  : `${ch.config.user || '—'} → ${ch.config.to || '—'}`

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// ---- 数据加载 ----

async function loadRules() {
  const r = await apiFetch('/api/alert-rules')
  rules.value = (await r.json()).rules
}
async function loadChannels() {
  const r = await apiFetch('/api/alert-channels')
  channels.value = (await r.json()).channels
}
async function loadHistory() {
  const r = await apiFetch('/api/alert-history')
  history.value = (await r.json()).history
}

onMounted(async () => {
  await loadChannels()
  await loadRules()
  await loadHistory()
})

// ---- 规则 CRUD ----

const ruleDialog = ref(false)
const ruleForm = reactive({
  id: null, name: '', metric: 'memory_pct', operator: '>=',
  threshold: 85, severity: 'warning', silence_minutes: 10,
  channel_ids: [], enabled: true,
})

function openRuleDialog(row = null) {
  if (row) {
    Object.assign(ruleForm, { ...row })
  } else {
    Object.assign(ruleForm, {
      id: null, name: '', metric: 'memory_pct', operator: '>=',
      threshold: 85, severity: 'warning', silence_minutes: 10,
      channel_ids: [], enabled: true,
    })
  }
  ruleDialog.value = true
}

async function saveRule() {
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请输入规则名称')
    return
  }
  const body = {
    name: ruleForm.name, metric: ruleForm.metric,
    operator: ruleForm.operator, threshold: ruleForm.threshold,
    severity: ruleForm.severity, silence_minutes: ruleForm.silence_minutes,
    channel_ids: ruleForm.channel_ids, enabled: ruleForm.enabled,
  }
  const url = ruleForm.id ? `/api/alert-rules/${ruleForm.id}` : '/api/alert-rules'
  const method = ruleForm.id ? 'PUT' : 'POST'
  const r = await apiFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) { ElMessage.error('保存失败'); return }
  ruleDialog.value = false
  await loadRules()
  ElMessage.success('已保存')
}

async function toggleRule(row) {
  const body = { name: row.name, metric: row.metric, operator: row.operator,
    threshold: row.threshold, severity: row.severity,
    silence_minutes: row.silence_minutes, channel_ids: row.channel_ids,
    enabled: !row.enabled }
  await apiFetch(`/api/alert-rules/${row.id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  await loadRules()
}

async function deleteRule(id) {
  await ElMessageBox.confirm('确定删除该规则？', '确认', { type: 'warning' })
  await apiFetch(`/api/alert-rules/${id}`, { method: 'DELETE' })
  await loadRules()
  ElMessage.success('已删除')
}

// ---- 渠道 CRUD ----

const chDialog = ref(false)
const chForm = reactive({
  id: null, name: '', type: 'webhook', enabled: true,
  config: { url: '', method: 'POST', use_tls: true, host: '', port: 465, user: '', password: '', to: '' },
  headersRaw: '',
})

function openChDialog(row = null) {
  if (row) {
    Object.assign(chForm, {
      id: row.id, name: row.name, type: row.type, enabled: row.enabled,
      config: {
        url: row.config.url ?? '',
        method: row.config.method ?? 'POST',
        use_tls: row.config.use_tls ?? true,
        host: row.config.host ?? '',
        port: row.config.port ?? 465,
        user: row.config.user ?? '',
        password: row.config.password ?? '',
        to: row.config.to ?? '',
      },
      headersRaw: row.config.headers ? JSON.stringify(row.config.headers, null, 2) : '',
    })
  } else {
    Object.assign(chForm, {
      id: null, name: '', type: 'webhook', enabled: true,
      config: { url: '', method: 'POST', use_tls: true, host: '', port: 465, user: '', password: '', to: '' },
      headersRaw: '',
    })
  }
  chDialog.value = true
}

function buildChConfig() {
  if (chForm.type === 'webhook') {
    let headers = {}
    try { headers = chForm.headersRaw.trim() ? JSON.parse(chForm.headersRaw) : {} } catch {}
    return { url: chForm.config.url, method: chForm.config.method, headers }
  }
  return {
    host: chForm.config.host, port: chForm.config.port,
    user: chForm.config.user, password: chForm.config.password,
    to: chForm.config.to, use_tls: chForm.config.use_tls,
  }
}

async function saveChannel() {
  if (!chForm.name.trim()) { ElMessage.warning('请输入渠道名称'); return }
  const body = { name: chForm.name, type: chForm.type, config: buildChConfig(), enabled: chForm.enabled }
  const url = chForm.id ? `/api/alert-channels/${chForm.id}` : '/api/alert-channels'
  const method = chForm.id ? 'PUT' : 'POST'
  const r = await apiFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) { ElMessage.error('保存失败'); return }
  chDialog.value = false
  await loadChannels()
  ElMessage.success('已保存')
}

async function toggleChannel(row) {
  const body = { name: row.name, type: row.type, config: row.config, enabled: !row.enabled }
  await apiFetch(`/api/alert-channels/${row.id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  await loadChannels()
}

async function deleteChannel(id) {
  await ElMessageBox.confirm('确定删除该渠道？', '确认', { type: 'warning' })
  await apiFetch(`/api/alert-channels/${id}`, { method: 'DELETE' })
  await loadChannels()
  await loadRules()
  ElMessage.success('已删除')
}

async function testChannel(row) {
  const r = await apiFetch(`/api/alert-channels/${row.id}/test`, { method: 'POST' })
  const body = await r.json()
  if (body.ok) {
    ElMessage.success(`测试推送成功：${body.message}`)
  } else {
    ElMessage.error(`测试推送失败：${body.message}`)
  }
}

// ---- 历史 ----

async function clearHistory() {
  await ElMessageBox.confirm('确定清空所有告警历史？', '确认', { type: 'warning' })
  await apiFetch('/api/alert-history', { method: 'DELETE' })
  await loadHistory()
  ElMessage.success('已清空')
}
</script>

<style scoped>
.alerts-page { flex: 1; overflow-y: auto; }
.alerts-inner { max-width: 1100px; margin: 0 auto; padding: 20px 24px 40px; }
.page-title { color: #e6edf3; font-size: 16px; margin: 0 0 16px; font-weight: 600; }
.toolbar { margin-bottom: 10px; }
.tbl { width: 100%; }
.empty { color: #484f58; font-size: 12px; padding: 20px 0; text-align: center; }
.dim { color: #8b949e; }
.small { font-size: 12px; }
.ch-tag { margin: 0 3px 2px 0; }
.main-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
</style>
