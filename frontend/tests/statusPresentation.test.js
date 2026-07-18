import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function componentSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('任务活动不把模型推理描述成网络连接或无响应', () => {
  const source = componentSource('../src/views/ChatView.vue')
  assert.match(source, /thinking-dots/)
  assert.match(source, /activityIndicator === 'confirmation'.*name="lock"/)
  assert.match(source, /activityIndicator === 'stopped'.*name="close"/)
  assert.match(source, /停止本轮处理/)
  assert.match(source, /class="activity-copy" aria-live="polite" aria-atomic="true"/)
  assert.match(source, /class="activity-timer" aria-hidden="true"/)
  assert.match(source, /watch\(running,[\s\S]*?\{ immediate: true \}\)/)
  assert.doesNotMatch(source, /正在连接规划模型|模型服务暂时没有响应|安全检查暂时没有响应/)
})

test('模型配置严格区分加载、失败和真正的空状态', () => {
  const source = componentSource('../src/views/ModelSettingsView.vue')
  const loading = source.indexOf('v-else-if="modelConfigLoading"')
  const failed = source.indexOf('v-else-if="modelConfigLoadError"')
  const empty = source.indexOf('class="empty-providers"')
  assert.ok(loading >= 0 && loading < failed)
  assert.ok(failed < empty)
  assert.match(source, /模型配置暂时未加载/)
  assert.match(source, /模型配置刷新未完成/)
  assert.match(source, /当前显示最近一次成功读取的配置/)
  assert.match(source, /重新加载/)
})

test('扩展配置加载失败时不再同时展示两个空列表', () => {
  const source = componentSource('../src/views/ExtensionsView.vue')
  assert.match(source, /正在同步扩展配置/)
  assert.equal((source.match(/v-else-if="!extensionsLoading && !extensionError"/g) || []).length, 2)
  assert.match(source, /状态未同步/)
  assert.match(source, /mcpDetailAvailable\(row\)/)
})

test('全局权限状态区分同步中、同步失败和未同步', () => {
  const source = componentSource('../src/views/PolicyView.vue')
  for (const text of [
    '正在同步全局权限',
    '权限同步失败',
    '全局权限尚未同步',
  ]) assert.match(source, new RegExp(text))
  assert.doesNotMatch(source, /等待服务器支持/)
})

test('所有确认选择提交时都有明确反馈', () => {
  const source = componentSource('../src/components/ConfirmCard.vue')
  assert.match(source, /正在提交你的选择/)
  assert.match(source, /:loading="resolving === 'deny'"/)
  assert.match(source, /!\['deny', 'allow_once'\]\.includes\(resolving\)/)
})

test('告警只在服务端确认成功后从共享状态移除', () => {
  const panel = componentSource('../src/components/StatusPanel.vue')
  const store = componentSource('../src/composables/useAlerts.js')
  assert.match(panel, /acknowledgePendingAlert\(alert\)/)
  assert.match(panel, /:loading="ackingAlertIds\.has\(alert\.id\)"/)
  assert.match(store, /responses\.find\(\(response\) => !response\.ok\)/)
  assert.match(store, /pendingAlertAckingIds/)
  assert.ok(sourceIndex(store, 'if (failed)')
    < sourceIndex(store, 'pendingAlerts.value = pendingAlerts.value.filter'))
})

test('告警入口显示共享待处理数量且窄屏仍保留徽标', () => {
  const source = componentSource('../src/components/Sidebar.vue')
  assert.match(source, /v\.key === 'alerts' && pendingAlertBadge/)
  assert.match(source, /alertBadgeText\(pendingAlertCount\.value\)/)
  assert.match(source, /class="nav-badge"/)
  assert.match(source, /条待处理告警/)
  assert.match(source, /\.nav-badge \{[\s\S]*?position: absolute;/)
})

test('告警页默认展示待处理告警并可点击历史查看详情', () => {
  const source = componentSource('../src/views/AlertsView.vue')
  assert.match(source, /const tab = ref\('pending'\)/)
  assert.match(source, /<el-tab-pane name="pending">/)
  assert.match(source, /pendingAlertCount/)
  assert.match(source, /acknowledgePendingAlert\(alert\)/)
  assert.match(source, /acknowledgeAllPendingAlerts\(\)/)
  assert.match(source, /一键确认全部待处理告警/)
  assert.doesNotMatch(source, /is-danger/)
  assert.match(source, /@row-click="openHistoryDetail"/)
  assert.match(source, /class="compact-record history-record"/)
  assert.match(source, /v-model="historyDetailOpen"/)
  assert.match(source, /tabCountText\(pendingAlertCount\)/)
  assert.match(source, /resolveRuleChannels/)
  assert.doesNotMatch(source, /`#\$\{id\}`/)
  assert.match(source, /label="操作"/)
  assert.match(source, /@media \(max-width: 1280px\)/)
  for (const label of ['监控指标', '触发值', '通知渠道', '记录编号', '规则编号', '告警说明']) {
    assert.match(source, new RegExp(label))
  }
})

test('告警页面允许局部接口失败且不会把刷新失败误报成写入失败', () => {
  const source = componentSource('../src/views/AlertsView.vue')
  assert.match(source, /refreshSection/)
  assert.match(source, /results\.every\(\(result\) => !result\)/)
  assert.match(source, /ruleLoadError && !rules\.length/)
  assert.match(source, /channelLoadError && !channels\.length/)
  assert.match(source, /historyLoadError && !history\.length/)
  assert.match(source, /pendingAlertsError && !pendingAlertsLoaded/)
  assert.match(source, /&& !hasRetainedData/)
  assert.match(source, /规则已保存，但列表刷新失败/)
  assert.match(source, /渠道已删除，但关联列表刷新失败/)
  assert.match(source, /告警历史已清空，但列表刷新失败/)
  assert.doesNotMatch(source, /Promise\.all\(\[loadChannels\(\), loadRules\(\), loadHistory\(\)\]\)/)
})

function sourceIndex(source, fragment) {
  const index = source.indexOf(fragment)
  assert.ok(index >= 0, `missing source fragment: ${fragment}`)
  return index
}

test('告警渠道在提交前校验目标与邮件必填配置', () => {
  const source = componentSource('../src/views/AlertsView.vue')
  assert.match(source, /new URL\(rawUrl\)/)
  assert.match(source, /\['http:', 'https:'\]\.includes\(url\.protocol\)/)
  assert.match(source, /chErrors\.host = '请输入 SMTP 主机'/)
  assert.match(source, /chErrors\.user = '请输入发件人账号'/)
  assert.match(source, /chErrors\.to = '请输入收件人'/)
  assert.match(source, /existingChannelPassword/)
  assert.match(source, /留空则保留现有凭据/)
})

test('总览页按端点局部降级且详细状态只受状态接口影响', () => {
  const source = componentSource('../src/views/DashboardView.vue')
  assert.match(source, /const endpointErrors = ref\(\{\}\)/)
  assert.match(source, /systemStatus as status/)
  assert.match(source, /sharedSystemStatusError/)
  assert.match(source, /refreshSystemStatus\(\)/)
  assert.doesNotMatch(source, /apiFetch\('\/api\/status'\)/)
  assert.match(source, /const statsError = computed\(\(\) => endpointErrors\.value\.stats/)
  assert.match(source, /未成功读取的项目暂不显示/)
  assert.match(source, /相关项目继续显示最近一次成功结果/)
  assert.match(source, /alerts: endpointLoaded\.alerts \|\| pendingAlertsLoaded\.value/)
  assert.match(source, /显示最近一次结果 · 刷新未完成/)
  assert.match(source, /class="metric-accessible"/)
})

test('系统状态弹窗与总览复用同一份状态快照', () => {
  const panel = componentSource('../src/components/StatusPanel.vue')
  const dashboard = componentSource('../src/views/DashboardView.vue')
  const shared = componentSource('../src/composables/useSystemStatus.js')
  assert.match(panel, /systemStatus as status/)
  assert.match(dashboard, /systemStatus as status/)
  assert.doesNotMatch(panel, /apiFetch\('\/api\/status'\)/)
  assert.doesNotMatch(dashboard, /apiFetch\('\/api\/status'\)/)
  assert.match(shared, /if \(refreshPromise\) return refreshPromise/)
  assert.match(shared, /startSystemStatusPolling/)
})

test('管理表格短状态列固定宽度且启用列位于操作列之前', () => {
  const alerts = componentSource('../src/views/AlertsView.vue')
  const models = componentSource('../src/views/ModelSettingsView.vue')
  assert.match(alerts, /label="通知" width="156"/)
  assert.match(models, /label="连接状态" width="150"/)
  assert.ok(sourceIndex(models, '<el-table-column label="启用" width="72"')
    < sourceIndex(models, '<el-table-column label="操作" width="218"'))
})

test('权限模式使用标准单选键盘导航并保留原切换流程', () => {
  const source = componentSource('../src/views/PolicyView.vue')
  assert.match(source, /:tabindex="permissionModeTabIndex\(mode\.value\)"/)
  assert.match(source, /@keydown="handlePermissionModeKeydown\(\$event, mode\.value\)"/)
  assert.match(source, /case 'ArrowRight':/)
  assert.match(source, /case 'ArrowLeft':/)
  assert.match(source, /case 'Home':/)
  assert.match(source, /case 'End':/)
  assert.match(source, /await choosePermissionMode\(targetMode\)/)
  assert.match(source, /:aria-busy="permissionChanging"/)
  assert.doesNotMatch(source, /full-access-visibility|显示“完全访问”高风险模式/)
})

test('审计页明确区分校验失败、读取失败与范围局部失败', () => {
  const source = componentSource('../src/views/AuditView.vue')
  assert.match(source, /if \(loading\.value\) return '正在校验'/)
  assert.match(source, /if \(loadError\.value\) return '校验未完成'/)
  assert.match(source, /Promise\.allSettled/)
  assert.match(source, /llm_provider_created: '已添加模型提供商'/)
  assert.match(source, /skill_selection_rejected: 'Skill 切换被拒绝'/)
  assert.match(source, /full_access_visibility_changed: '完全访问入口变更'/)
  assert.match(source, /已显示高风险入口/)
})
