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

test('权限状态区分同步中、同步失败、未同步和任务草稿', () => {
  const source = componentSource('../src/views/PolicyView.vue')
  for (const text of [
    '正在同步当前任务权限',
    '权限同步失败',
    '当前任务权限尚未同步',
    '新任务设置将在创建时应用',
  ]) assert.match(source, new RegExp(text))
  assert.doesNotMatch(source, /等待服务器支持/)
})

test('所有确认选择提交时都有明确反馈', () => {
  const source = componentSource('../src/components/ConfirmCard.vue')
  assert.match(source, /正在提交你的选择/)
  assert.match(source, /:loading="resolving === 'deny'"/)
  assert.match(source, /!\['deny', 'allow_once'\]\.includes\(resolving\)/)
})
