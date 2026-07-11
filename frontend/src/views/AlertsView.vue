<template>
  <div class="kg-page alerts-page">
    <div class="kg-page-inner alerts-inner">
      <header class="page-head">
        <div>
          <p class="page-description">设置触发条件、通知渠道，并查看告警记录。</p>
        </div>
        <div class="page-actions">
          <el-button v-if="tab === 'rules'" type="primary" @click="openRuleDialog()">
            <KgIcon name="plus" :size="15" />
            新建规则
          </el-button>
          <el-button v-else-if="tab === 'channels'" type="primary" @click="openChDialog()">
            <KgIcon name="plus" :size="15" />
            新建渠道
          </el-button>
          <el-button
            v-else
            text
            type="danger"
            :disabled="!history.length"
            @click="clearHistory"
          >清空历史</el-button>
        </div>
      </header>

      <el-tabs v-model="tab" class="main-tabs">
        <el-tab-pane name="rules">
          <template #label>
            <span class="tab-label">规则 <span>{{ rules.length }}</span></span>
          </template>

          <template v-if="rules.length">
            <el-table :data="rules" class="wide-table alert-table">
              <el-table-column label="规则名称" prop="name" min-width="145" />
              <el-table-column label="指标" min-width="150">
                <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
              </el-table-column>
              <el-table-column label="条件" width="116">
                <template #default="{ row }"><code class="condition">{{ conditionText(row) }}</code></template>
              </el-table-column>
              <el-table-column label="严重度" width="92">
                <template #default="{ row }">
                  <span class="severity" :class="row.severity">
                    <span class="severity-dot"></span>{{ severityLabel(row.severity) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="沉默期" width="86">
                <template #default="{ row }">{{ row.silence_minutes }} 分钟</template>
              </el-table-column>
              <el-table-column label="通知" min-width="150">
                <template #default="{ row }">
                  <span v-if="!row.channel_ids.length" class="muted">仅记录</span>
                  <span v-else class="channel-list">
                    <span v-for="channelId in row.channel_ids" :key="channelId" class="channel-chip">
                      {{ channelName(channelId) }}
                    </span>
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="66" align="center">
                <template #default="{ row }">
                  <el-switch :model-value="row.enabled" size="small" @change="toggleRule(row)" />
                </template>
              </el-table-column>
              <el-table-column label="" width="106" align="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <el-button text @click="openRuleDialog(row)">编辑</el-button>
                    <el-button text type="danger" @click="deleteRule(row.id)">删除</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <div class="compact-list rules-compact">
              <article v-for="rule in rules" :key="rule.id" class="compact-record">
                <div class="compact-head">
                  <strong>{{ rule.name }}</strong>
                  <span class="severity" :class="rule.severity">
                    <span class="severity-dot"></span>{{ severityLabel(rule.severity) }}
                  </span>
                  <el-switch :model-value="rule.enabled" size="small" @change="toggleRule(rule)" />
                </div>
                <div class="compact-meta">
                  <span>{{ metricLabel(rule.metric) }} {{ conditionText(rule) }}</span>
                  <span>沉默 {{ rule.silence_minutes }} 分钟</span>
                  <span>{{ channelSummary(rule) }}</span>
                </div>
                <div class="compact-actions">
                  <el-button text @click="openRuleDialog(rule)">编辑</el-button>
                  <el-button text type="danger" @click="deleteRule(rule.id)">删除</el-button>
                </div>
              </article>
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="bell" :size="24" />
            <strong>还没有告警规则</strong>
            <span>创建规则后，系统会按条件记录或推送告警。</span>
            <el-button @click="openRuleDialog()">新建规则</el-button>
          </div>
        </el-tab-pane>

        <el-tab-pane name="channels">
          <template #label>
            <span class="tab-label">渠道 <span>{{ channels.length }}</span></span>
          </template>

          <template v-if="channels.length">
            <el-table :data="channels" class="wide-table alert-table">
              <el-table-column label="渠道名称" prop="name" min-width="170" />
              <el-table-column label="类型" width="108">
                <template #default="{ row }">
                  <span class="type-badge">{{ channelTypeLabel(row.type) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="目标" min-width="310">
                <template #default="{ row }"><code class="target">{{ chTarget(row) }}</code></template>
              </el-table-column>
              <el-table-column label="状态" width="70" align="center">
                <template #default="{ row }">
                  <el-switch :model-value="row.enabled" size="small" @change="toggleChannel(row)" />
                </template>
              </el-table-column>
              <el-table-column label="" width="164" align="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <el-button text @click="testChannel(row)">测试</el-button>
                    <el-button text @click="openChDialog(row)">编辑</el-button>
                    <el-button text type="danger" @click="deleteChannel(row.id)">删除</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <div class="compact-list channels-compact">
              <article v-for="channel in channels" :key="channel.id" class="compact-record">
                <div class="compact-head">
                  <strong>{{ channel.name }}</strong>
                  <span class="type-badge">{{ channelTypeLabel(channel.type) }}</span>
                  <el-switch :model-value="channel.enabled" size="small" @change="toggleChannel(channel)" />
                </div>
                <code class="compact-target">{{ chTarget(channel) }}</code>
                <div class="compact-actions">
                  <el-button text @click="testChannel(channel)">测试</el-button>
                  <el-button text @click="openChDialog(channel)">编辑</el-button>
                  <el-button text type="danger" @click="deleteChannel(channel.id)">删除</el-button>
                </div>
              </article>
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="activity" :size="24" />
            <strong>还没有推送渠道</strong>
            <span>未绑定渠道的规则仍会记录在告警历史中。</span>
            <el-button @click="openChDialog()">新建渠道</el-button>
          </div>
        </el-tab-pane>

        <el-tab-pane name="history">
          <template #label>
            <span class="tab-label">历史 <span>{{ history.length }}</span></span>
          </template>

          <template v-if="history.length">
            <el-table :data="history" class="wide-table alert-table history-table">
              <el-table-column label="时间" width="150">
                <template #default="{ row }"><span class="time-text">{{ fmtTime(row.fired_at) }}</span></template>
              </el-table-column>
              <el-table-column label="规则" prop="rule_name" min-width="140" />
              <el-table-column label="指标" min-width="145">
                <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
              </el-table-column>
              <el-table-column label="值" width="74">
                <template #default="{ row }"><code class="condition">{{ metricValueText(row) }}</code></template>
              </el-table-column>
              <el-table-column label="严重度" width="90">
                <template #default="{ row }">
                  <span class="severity" :class="row.severity">
                    <span class="severity-dot"></span>{{ severityLabel(row.severity) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="已通知" min-width="140">
                <template #default="{ row }">
                  <span class="muted">{{ notifiedSummary(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="说明" prop="message" min-width="210">
                <template #default="{ row }"><span class="history-message">{{ row.message }}</span></template>
              </el-table-column>
            </el-table>

            <div class="compact-list history-compact">
              <article v-for="item in history" :key="`${item.fired_at}-${item.rule_name}`" class="compact-record">
                <div class="compact-head">
                  <strong>{{ item.rule_name }}</strong>
                  <span class="severity" :class="item.severity">
                    <span class="severity-dot"></span>{{ severityLabel(item.severity) }}
                  </span>
                  <time>{{ fmtTime(item.fired_at) }}</time>
                </div>
                <div class="compact-meta">
                  <span>{{ metricLabel(item.metric) }}：{{ metricValueText(item) }}</span>
                  <span>{{ notifiedSummary(item) }}</span>
                </div>
                <p class="compact-message">{{ item.message }}</p>
              </article>
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="check" :size="24" />
            <strong>暂时没有告警记录</strong>
            <span>规则触发后，记录会显示在这里。</span>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog
      v-model="ruleDialog"
      :title="ruleForm.id ? '编辑规则' : '新建规则'"
      width="520px"
      align-center
    >
      <el-form :model="ruleForm" label-position="top" class="dialog-form">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" placeholder="例如：内存使用率过高" />
        </el-form-item>

        <el-form-item label="监控指标">
          <el-select v-model="ruleForm.metric" style="width: 100%">
            <el-option v-for="metric in METRICS" :key="metric.value" :value="metric.value" :label="metric.label" />
          </el-select>
        </el-form-item>

        <el-form-item label="触发条件">
          <div v-if="ruleForm.metric === 'failed_services'" class="static-condition">
            存在停止的自动启动服务
          </div>
          <div v-else class="condition-editor">
            <el-select v-model="ruleForm.operator" class="operator-select">
              <el-option value=">=" label=">=" />
              <el-option value=">" label=">" />
              <el-option value="<=" label="<=" />
              <el-option value="<" label="<" />
            </el-select>
            <el-input-number v-model="ruleForm.threshold" :min="0" :max="100" controls-position="right" />
            <span>%</span>
          </div>
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="严重度">
            <el-radio-group v-model="ruleForm.severity">
              <el-radio value="warning">警告</el-radio>
              <el-radio value="critical">严重</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="沉默期">
            <div class="number-field">
              <el-input-number v-model="ruleForm.silence_minutes" :min="1" :max="1440" controls-position="right" />
              <span>分钟</span>
            </div>
          </el-form-item>
        </div>

        <el-form-item label="推送渠道">
          <el-select
            v-model="ruleForm.channel_ids"
            multiple
            style="width: 100%"
            placeholder="不选择则仅记录"
          >
            <el-option v-for="channel in channels" :key="channel.id" :value="channel.id" :label="channel.name" />
          </el-select>
        </el-form-item>

        <div class="enabled-row">
          <div>
            <strong>启用规则</strong>
            <span>保存后立即参与告警评估。</span>
          </div>
          <el-switch v-model="ruleForm.enabled" />
        </div>
      </el-form>

      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" @click="saveRule">保存规则</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="chDialog"
      :title="chForm.id ? '编辑渠道' : '新建渠道'"
      width="560px"
      align-center
    >
      <el-form :model="chForm" label-position="top" class="dialog-form">
        <el-form-item label="渠道名称">
          <el-input v-model="chForm.name" placeholder="例如：运维群 Webhook" />
        </el-form-item>

        <el-form-item label="渠道类型">
          <el-radio-group v-model="chForm.type">
            <el-radio-button value="webhook">Webhook</el-radio-button>
            <el-radio-button value="email">邮件</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="chForm.type === 'webhook'">
          <el-form-item label="URL">
            <el-input v-model="chForm.config.url" placeholder="https://..." />
          </el-form-item>
          <el-form-item label="HTTP 方法">
            <el-select v-model="chForm.config.method" style="width: 140px">
              <el-option value="POST" label="POST" />
              <el-option value="PUT" label="PUT" />
            </el-select>
          </el-form-item>
          <el-form-item label="自定义 Header">
            <el-input
              v-model="chForm.headersRaw"
              type="textarea"
              :rows="4"
              class="headers-input"
              placeholder='{"Authorization": "Bearer xxx"}'
            />
          </el-form-item>
        </template>

        <template v-else>
          <div class="form-grid">
            <el-form-item label="SMTP 主机">
              <el-input v-model="chForm.config.host" placeholder="smtp.example.com" />
            </el-form-item>
            <el-form-item label="端口">
              <el-input-number v-model="chForm.config.port" :min="1" :max="65535" controls-position="right" />
            </el-form-item>
          </div>
          <el-form-item label="连接加密">
            <el-switch v-model="chForm.config.use_tls" active-text="使用 SSL/TLS" />
          </el-form-item>
          <el-form-item label="发件人账号">
            <el-input v-model="chForm.config.user" placeholder="noreply@example.com" />
          </el-form-item>
          <el-form-item label="授权码或密码">
            <el-input v-model="chForm.config.password" type="password" show-password />
          </el-form-item>
          <el-form-item label="收件人">
            <el-input v-model="chForm.config.to" placeholder="ops@example.com" />
          </el-form-item>
        </template>

        <div class="enabled-row">
          <div>
            <strong>启用渠道</strong>
            <span>停用后将不再向该渠道推送。</span>
          </div>
          <el-switch v-model="chForm.enabled" />
        </div>
      </el-form>

      <template #footer>
        <el-button @click="chDialog = false">取消</el-button>
        <el-button type="primary" @click="saveChannel">保存渠道</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useApi.js'

const tab = ref('rules')
const rules = ref([])
const channels = ref([])
const history = ref([])

const METRICS = [
  { value: 'memory_pct', label: '内存使用率 (%)' },
  { value: 'cpu_pct', label: 'CPU 使用率 (%)' },
  { value: 'disk_pct', label: '磁盘使用率 (%)（任意盘）' },
  { value: 'failed_services', label: '停止的自动启动服务' },
]

const metricLabel = (value) => METRICS.find(metric => metric.value === value)?.label ?? value
const severityLabel = (value) => ({ warning: '警告', critical: '严重' }[value] ?? value)
const channelTypeLabel = (value) => ({ webhook: 'Webhook', email: '邮件' }[value] ?? value)
const channelName = (id) => channels.value.find(channel => channel.id === id)?.name ?? `#${id}`
const chTarget = (channel) => channel.type === 'webhook'
  ? channel.config.url || '—'
  : `${channel.config.user || '—'} → ${channel.config.to || '—'}`

const metricValueText = (item) => item.metric === 'failed_services'
  ? (Number.parseFloat(item.metric_value) > 0 || item.metric_value === '存在' ? '存在' : '无')
  : item.metric_value

function conditionText(rule) {
  if (rule.metric !== 'failed_services') return `${rule.operator} ${rule.threshold}%`
  return rule.operator === '>=' && Number(rule.threshold) === 1
    ? '存在失败服务'
    : `${rule.operator} ${rule.threshold}（0=无，1=有）`
}

function channelSummary(rule) {
  return rule.channel_ids.length ? rule.channel_ids.map(channelName).join('、') : '仅记录'
}

function notifiedSummary(item) {
  return item.channels_notified.length ? item.channels_notified.join('、') : '未推送'
}

function fmtTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

async function loadRules() {
  const response = await apiFetch('/api/alert-rules')
  rules.value = (await response.json()).rules
}

async function loadChannels() {
  const response = await apiFetch('/api/alert-channels')
  channels.value = (await response.json()).channels
}

async function loadHistory() {
  const response = await apiFetch('/api/alert-history')
  history.value = (await response.json()).history
}

onMounted(async () => {
  await loadChannels()
  await loadRules()
  await loadHistory()
})

const ruleDialog = ref(false)
const ruleForm = reactive({
  id: null, name: '', metric: 'memory_pct', operator: '>=',
  threshold: 85, severity: 'warning', silence_minutes: 10,
  channel_ids: [], enabled: true,
})

function openRuleDialog(row = null) {
  if (row) {
    Object.assign(ruleForm, { ...row, channel_ids: [...row.channel_ids] })
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
    operator: ruleForm.metric === 'failed_services' ? '>=' : ruleForm.operator,
    threshold: ruleForm.metric === 'failed_services' ? 1 : ruleForm.threshold,
    severity: ruleForm.severity, silence_minutes: ruleForm.silence_minutes,
    channel_ids: ruleForm.channel_ids, enabled: ruleForm.enabled,
  }
  const url = ruleForm.id ? `/api/alert-rules/${ruleForm.id}` : '/api/alert-rules'
  const method = ruleForm.id ? 'PUT' : 'POST'
  const response = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    ElMessage.error('保存失败')
    return
  }
  ruleDialog.value = false
  await loadRules()
  ElMessage.success('已保存')
}

async function toggleRule(row) {
  const body = {
    name: row.name, metric: row.metric, operator: row.operator,
    threshold: row.threshold, severity: row.severity,
    silence_minutes: row.silence_minutes, channel_ids: row.channel_ids,
    enabled: !row.enabled,
  }
  await apiFetch(`/api/alert-rules/${row.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await loadRules()
}

async function deleteRule(id) {
  await ElMessageBox.confirm('确定删除该规则？', '确认', { type: 'warning' })
  await apiFetch(`/api/alert-rules/${id}`, { method: 'DELETE' })
  await loadRules()
  ElMessage.success('已删除')
}

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
    try {
      headers = chForm.headersRaw.trim() ? JSON.parse(chForm.headersRaw) : {}
    } catch {
      // 保持原行为：无效 JSON 作为空 Header 保存。
    }
    return { url: chForm.config.url, method: chForm.config.method, headers }
  }
  return {
    host: chForm.config.host, port: chForm.config.port,
    user: chForm.config.user, password: chForm.config.password,
    to: chForm.config.to, use_tls: chForm.config.use_tls,
  }
}

async function saveChannel() {
  if (!chForm.name.trim()) {
    ElMessage.warning('请输入渠道名称')
    return
  }
  const body = { name: chForm.name, type: chForm.type, config: buildChConfig(), enabled: chForm.enabled }
  const url = chForm.id ? `/api/alert-channels/${chForm.id}` : '/api/alert-channels'
  const method = chForm.id ? 'PUT' : 'POST'
  const response = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    ElMessage.error('保存失败')
    return
  }
  chDialog.value = false
  await loadChannels()
  ElMessage.success('已保存')
}

async function toggleChannel(row) {
  const body = { name: row.name, type: row.type, config: row.config, enabled: !row.enabled }
  await apiFetch(`/api/alert-channels/${row.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
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
  const response = await apiFetch(`/api/alert-channels/${row.id}/test`, { method: 'POST' })
  const body = await response.json()
  if (body.ok) {
    ElMessage.success(`测试推送成功：${body.message}`)
  } else {
    ElMessage.error(`测试推送失败：${body.message}`)
  }
}

async function clearHistory() {
  await ElMessageBox.confirm('确定清空所有告警历史？', '确认', { type: 'warning' })
  await apiFetch('/api/alert-history', { method: 'DELETE' })
  await loadHistory()
  ElMessage.success('已清空')
}
</script>

<style scoped>
.alerts-inner { width: min(100%, 1120px); }

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--kg-space-6);
}

.page-description {
  margin: 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
}

.page-actions :deep(.el-button) { gap: 7px; }
.main-tabs { margin-top: var(--kg-space-5); }
.main-tabs :deep(.el-tabs__header) { margin-bottom: var(--kg-space-3); }

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tab-label > span {
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 10px;
  line-height: 18px;
  text-align: center;
}

.alert-table { width: 100%; }

.condition,
.target,
.time-text {
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

.target {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time-text { color: var(--kg-text-tertiary); }

.severity {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--kg-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.severity.warning { color: var(--kg-warning); }
.severity.critical { color: var(--kg-danger); }

.severity-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.channel-list { display: flex; flex-wrap: wrap; gap: 4px; }

.channel-chip,
.type-badge {
  display: inline-flex;
  align-items: center;
  min-height: 21px;
  padding: 1px 6px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-xs);
  color: var(--kg-text-secondary);
  font-size: 11px;
}

.type-badge { color: var(--kg-info); }
.muted { color: var(--kg-text-disabled); font-size: 12px; }

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  white-space: nowrap;
}

.row-actions :deep(.el-button + .el-button) { margin-left: 0; }

.history-message {
  display: block;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alerts-empty {
  min-height: 230px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.compact-list { display: none; }

.compact-record {
  padding: var(--kg-space-3) 0;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.compact-head {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.compact-head strong {
  min-width: 0;
  overflow: hidden;
  color: var(--kg-text-primary);
  font-size: 13px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-head > :last-child { margin-left: auto; }
.compact-head time { margin-left: auto; color: var(--kg-text-tertiary); font-family: var(--kg-font-mono); font-size: 11px; }

.compact-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 14px;
  margin-top: 7px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.compact-target {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.compact-actions :deep(.el-button + .el-button) { margin-left: 0; }

.compact-message {
  margin: 6px 0 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
}

.dialog-form :deep(.el-form-item) { margin-bottom: 17px; }
.dialog-form :deep(.el-form-item__label) { margin-bottom: 6px; color: var(--kg-text-secondary); font-size: 12px; line-height: 18px; }

.condition-editor,
.number-field {
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  width: 100%;
}

.operator-select { width: 96px; }
.condition-editor :deep(.el-input-number) { flex: 1; width: auto; }
.condition-editor > span,
.number-field > span { color: var(--kg-text-tertiary); font-size: 12px; }

.static-condition {
  width: 100%;
  min-height: 32px;
  padding: 6px 10px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-secondary);
  font-size: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--kg-space-4);
}

.form-grid :deep(.el-input-number) { width: 100%; }

.enabled-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-5);
  min-height: 54px;
  padding: 9px 12px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.enabled-row strong {
  display: block;
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 550;
}

.enabled-row span {
  display: block;
  margin-top: 2px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.headers-input :deep(.el-textarea__inner) {
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

@media (max-width: 1080px) {
  .wide-table { display: none; }
  .compact-list { display: grid; }
}
</style>
