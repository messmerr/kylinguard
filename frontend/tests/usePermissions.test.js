import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
}

let fetchImpl = async (url) => {
  throw new Error(`unexpected fetch: ${url}`)
}
globalThis.fetch = (url, options) => fetchImpl(url, options)

const permissions = await import('../src/composables/usePermissions.js')
const policyViewSource = await readFile(
  new URL('../src/views/PolicyView.vue', import.meta.url), 'utf8',
)
const permissionSelectorSource = await readFile(
  new URL('../src/components/PermissionSelector.vue', import.meta.url), 'utf8',
)
const fullAccessWarningsSource = await readFile(
  new URL('../src/utils/fullAccessWarnings.js', import.meta.url), 'utf8',
)
const chatViewSource = await readFile(
  new URL('../src/views/ChatView.vue', import.meta.url), 'utf8',
)

function reset() {
  permissions._resetPermissionStateForTests()
  fetchImpl = async (url) => { throw new Error(`unexpected fetch: ${url}`) }
}

test('新会话保留全局审批设置，但收回上一会话动作授权', () => {
  reset()
  permissions.permissionContext.mode = 'full_access'
  permissions.permissionContext.autoReviewRoots = ['/srv/global']
  permissions.recordLocalGrant({
    id: 'session-root', path: '/srv/session', actions: ['create'], lifetime: 'session',
  })
  permissions.beginNewPermissionSession()

  assert.equal(permissions.permissionMode.value, 'full_access')
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'unknown')
  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/global'])
  assert.deepEqual(permissions.permissionGrants.value, [])
})

test('完全访问文案明确完整能力且不把 Reviewer 描述成硬否决', () => {
  const fullAccess = permissions.PERMISSION_MODES.find((mode) => mode.value === 'full_access')
  assert.match(fullAccess.description, /shell.*文件.*网络.*进程/)
  assert.match(fullAccess.description, /不再逐项确认/)
  assert.doesNotMatch(fullAccess.description, /Reviewer|独立安全复核|硬拒绝/)
  assert.doesNotMatch(fullAccess.description, /控制面隔离/)
})

test('内置策略页面按风险分类解释完整 Shell 能力', () => {
  assert.match(policyViewSource, /高风险命令模式/)
  assert.match(policyViewSource, /不会仅因提权或启动子 Shell 而直接拒绝/)
  assert.match(policyViewSource, /普通模式会拦截或复核显式控制面路径/)
  assert.match(policyViewSource, /完全访问可覆盖产品层路径限制/)
  assert.match(policyViewSource, /不代表出现即拒绝/)
  assert.doesNotMatch(policyViewSource, /以下执行器会被直接拒绝|对这些路径的写操作会被拒绝/)
})

test('权限界面用不同 UID 表达账户分离并显著显示 root 警告', () => {
  assert.match(policyViewSource, /执行账户 UID/)
  assert.match(policyViewSource, /permissionContext\.grantsRoot/)
  assert.match(policyViewSource, /该执行身份拥有 root 权限/)
  assert.match(permissionSelectorSource, /root-badge/)
  assert.match(permissionSelectorSource, /将获得 root 权限/)
  assert.doesNotMatch(policyViewSource, /OS 账户隔离|ACL 控制面隔离/)
})

test('两个权限入口复用全局权限编排', () => {
  assert.match(permissionSelectorSource, /setChatPermissionMode\(mode/)
  assert.match(policyViewSource, /setChatPermissionMode\(mode/)
  assert.match(permissionSelectorSource, /visiblePermissionModes/)
  assert.match(policyViewSource, /setFullAccessVisibility/)
  assert.match(policyViewSource, /显示“完全访问”高风险模式/)
  assert.doesNotMatch(permissionSelectorSource, /发送第一条消息后可开启/)
  assert.doesNotMatch(policyViewSource, /发送第一条消息创建任务后/)
})

test('完全访问默认隐藏且显示与启用各有独立醒目二次确认', () => {
  reset()
  assert.equal(permissions.permissionContext.fullAccessVisible, false)
  assert.equal(
    permissions.visiblePermissionModes.value.some((mode) => mode.value === 'full_access'),
    false,
  )
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', version: 2,
    full_access_visible: true,
  })
  assert.equal(
    permissions.visiblePermissionModes.value.some((mode) => mode.value === 'full_access'),
    true,
  )
  assert.match(fullAccessWarningsSource, /显示高风险权限入口 · 第 1\/2 步/)
  assert.match(fullAccessWarningsSource, /显示高风险权限入口 · 第 2\/2 步/)
  assert.match(fullAccessWarningsSource, /启用完全访问 · 第 1\/2 步/)
  assert.match(fullAccessWarningsSource, /启用完全访问 · 第 2\/2 步/)
  assert.match(fullAccessWarningsSource, /'显示完全访问'/)
  assert.match(fullAccessWarningsSource, /'启用完全访问'/)
  assert.match(fullAccessWarningsSource, /closeOnClickModal: false/)
  assert.match(fullAccessWarningsSource, /closeOnPressEscape: false/)
})

test('新任务 composer 明确选择服务器工作目录且已有任务锁定', () => {
  assert.match(chatViewSource, /设置服务器工作目录/)
  assert.match(chatViewSource, /不是浏览器本地文件夹，也不是安全沙箱/)
  assert.match(chatViewSource, /:disabled="composerDisabled \|\| Boolean\(activeId\)"/)
  assert.match(chatViewSource, /running\.value \|\| sessionLoading\.value/)
  assert.match(chatViewSource, /setDraftWorkspaceRoot/)
})

test('自动执行范围规范化去重并保存为全局设置', async () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', version: 1,
    workspace_root: '/srv/default', auto_review_roots: [],
  })
  const submitted = []
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/permissions')
    const body = JSON.parse(options.body)
    submitted.push(body)
    return Response.json({ ...body, version: body.version + 1 })
  }
  await permissions.addAutoReviewRoot('/srv/demo/../docs/')
  await permissions.addAutoReviewRoot('/srv/docs')
  await permissions.setPermissionMode('auto_review')

  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/docs'])
  assert.equal(submitted.length, 2)
  assert.deepEqual(submitted[1].auto_review_roots, ['/srv/docs'])
  assert.deepEqual(permissions.permissionRequestPayload(), { workspace_root: '/srv/default' })
})

test('从全局权限接口读取模式，并按当前会话读取动作授权', async () => {
  reset()
  fetchImpl = async (url) => {
    if (url === '/api/permissions') {
      return Response.json({
        mode: 'auto_review', version: 3,
        execution_identity: 'backend-user', execution_identity_source: 'backend_process',
        workspace_root: '/srv/default', command_shell: '/bin/bash',
        command_max_timeout: 900, execution_account_separated: false,
        control_plane_isolated: true, grants_root: true,
        full_access_available: true,
        auto_review_roots: ['/srv/project'],
      })
    }
    if (url === '/api/sessions/session-1/grants') {
      return Response.json({ grants: [{
        id: 'g1', resource: '/srv/project/report.md',
        capability: 'files.write', scope: 'session',
      }] })
    }
    throw new Error(`unexpected fetch: ${url}`)
  }

  permissions.bindPermissionSession('session-1', { workspaceRoot: '/srv/session-project' })
  const result = await permissions.loadPermissionContext('session-1')

  assert.equal(result.supported, true)
  assert.equal(permissions.permissionMode.value, 'auto_review')
  assert.equal(permissions.permissionContext.version, 3)
  assert.equal(permissions.permissionContext.executorIdentity, 'backend-user')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'backend_process')
  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/session-project')
  assert.equal(permissions.permissionContext.defaultWorkspaceRoot, '/srv/default')
  assert.equal(permissions.permissionContext.commandShell, '/bin/bash')
  assert.equal(permissions.permissionContext.commandMaxTimeout, 900)
  assert.equal(permissions.permissionContext.executionAccountSeparated, false)
  assert.equal(permissions.permissionContext.grantsRoot, true)
  assert.equal(permissions.executionIdentitySourceLabel(), '后端当前 OS 身份')
  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/project'])
  assert.equal(permissions.permissionGrants.value.length, 1)
  assert.equal(permissions.permissionGrants.value[0].label, '/srv/project/report.md')
})

test('自动审核作为独立审批模式并明确故障与高风险回退', () => {
  const autoReview = permissions.PERMISSION_MODES.find((mode) => mode.value === 'auto_review')
  assert.ok(autoReview)
  assert.match(autoReview.description, /Reviewer/)
  assert.match(autoReview.description, /高风险.*破坏性.*越界.*审核异常/)
  assert.doesNotMatch(permissionSelectorSource, /trusted_workspace|信任目录/)
  assert.match(policyViewSource, /全局自动执行范围与会话授权/)
})

test('全局权限接口不可用时不把未保存选择显示成已生效', async () => {
  reset()
  permissions.bindPermissionSession('legacy-session')
  permissions.permissionContext.version = 1
  fetchImpl = async () => new Response(null, { status: 404 })

  await assert.rejects(
    permissions.setPermissionMode('read_only'), /全局权限设置接口/,
  )
  await assert.rejects(
    permissions.addAutoReviewRoot('/srv/legacy'), /全局权限设置接口/,
  )
  assert.equal(permissions.permissionMode.value, 'ask')
  assert.deepEqual(permissions.autoReviewRoots.value, [])
  assert.deepEqual(permissions.permissionGrants.value, [])
})

test('权限加载错误与权限修改错误使用独立展示状态', async () => {
  reset()
  fetchImpl = async () => Response.json({ detail: '权限服务不可用' }, { status: 500 })

  await assert.rejects(
    permissions.loadPermissionContext('session-load-error'),
    /权限服务不可用/,
  )

  assert.equal(permissions.permissionLoadError.value, '权限服务不可用')
})

test('完全访问升级集中通过 permissions PUT 且不携带时限', async () => {
  reset()
  permissions.bindPermissionSession('session-full')
  permissions.permissionContext.version = 1
  permissions.permissionContext.fullAccessVisible = true
  let submitted
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/permissions')
    assert.equal(options.method, 'PUT')
    submitted = JSON.parse(options.body)
    return Response.json({
      mode: 'full_access', version: 1,
      executor_identity: 'root', control_plane_isolated: true,
      grants_root: true,
    })
  }

  await permissions.setPermissionMode('full_access')

  assert.deepEqual(submitted, {
    mode: 'full_access', version: 1, auto_review_roots: [],
  })
  assert.equal(permissions.fullAccessActive.value, true)
  assert.equal(permissions.permissionContext.executorIdentity, 'root')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'legacy')
  assert.equal(permissions.executionIdentitySourceLabel(), '旧版 API（身份来源未说明）')
  assert.equal(permissions.permissionContext.executionAccountSeparated, true)
  assert.equal(permissions.permissionContext.grantsRoot, true)
})

test('隐藏状态禁止直接启用，入口揭示通过独立端点并更新版本', async () => {
  reset()
  permissions.permissionContext.version = 1
  await assert.rejects(
    permissions.setPermissionMode('full_access'),
    /隐藏状态/,
  )

  let submitted
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/permissions/full-access-visibility')
    submitted = JSON.parse(options.body)
    return Response.json({
      mode: 'ask', version: 1, full_access_visible: true,
      full_access_available: true,
    })
  }

  await permissions.setFullAccessVisibility(true)

  assert.deepEqual(submitted, { visible: true, version: 1 })
  assert.equal(permissions.permissionContext.fullAccessVisible, true)
  assert.equal(permissions.permissionContext.version, 1)
})

test('完全访问警告明确持续生效及收回条件', () => {
  assert.match(fullAccessWarningsSource, /持续生效/)
  assert.match(fullAccessWarningsSource, /手动收回.*隐藏入口.*服务端.*后端重启/)
  assert.doesNotMatch(fullAccessWarningsSource, /分钟后收回/)
})

test('动作授权即使资源是路径也不会扩大自动执行范围', () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', executor_user: 'ops', version: 2,
  })
  permissions.applyPermissionEvent({
    type: 'permission_grant_created',
    grant: { id: 'g2', path: '/var/reports', actions: ['write'], lifetime: 'session' },
  })
  assert.deepEqual(permissions.autoReviewRoots.value, [])
  assert.equal(permissions.permissionGrants.value.length, 1)

  permissions.applyPermissionEvent({ type: 'permission_revoked', grant_id: 'g2' })
  assert.deepEqual(permissions.autoReviewRoots.value, [])
  assert.equal(permissions.permissionGrants.value.length, 0)
  assert.equal(permissions.permissionContext.executorIdentity, 'ops')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'legacy')
})

test('完全访问不会由前端倒计时自动收回', () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'full_access', version: 2,
  })
  assert.equal(permissions.permissionMode.value, 'full_access')
  assert.equal(permissions.fullAccessActive.value, true)
})

test('新权限请求与旧确认接口都支持带范围的授权决断', async () => {
  reset()
  const calls = []
  fetchImpl = async (url, options = {}) => {
    calls.push({ url, body: JSON.parse(options.body) })
    if (url.startsWith('/api/permission-requests/')) {
      return Response.json({ grant: {
        id: 'server-grant', path: '/srv/docs', actions: ['modify'], lifetime: 'session',
      } })
    }
    if (url === '/api/confirm') return Response.json({ ok: true })
    throw new Error(`unexpected fetch: ${url}`)
  }

  const scope = { kind: 'path', path: '/srv/docs', actions: ['modify'], recursive: true }
  await permissions.resolvePermissionRequest(
    { permissionRequestId: 'request-1', contextVersion: 2 }, 'allow_session',
    scope,
  )
  await permissions.resolvePermissionRequest(
    { confirmId: 'confirm-1' }, 'authorize_path', scope,
  )

  assert.equal(calls[0].url, '/api/permission-requests/request-1/resolve')
  assert.deepEqual(calls[0].body, {
    decision: 'allow_session', context_version: 2, authorized_path: '',
  })
  assert.equal(calls[1].url, '/api/confirm')
  assert.equal(calls[1].body.approved, true)
  assert.equal(calls[1].body.decision, 'authorize_path')
  assert.deepEqual(permissions.autoReviewRoots.value, [])
})

test('撤销文件动作授权走 grant DELETE，不改自动执行范围', async () => {
  reset()
  permissions.bindPermissionSession('session-grant')
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'auto_review', version: 2,
    auto_review_roots: ['/srv/work'],
  })
  permissions.recordLocalGrant({
    id: 'grant-file', resource: '/etc/report.md', capability: 'files.write',
    scope: 'session',
  })
  let deletedUrl = ''
  fetchImpl = async (url, options = {}) => {
    deletedUrl = url
    assert.equal(options.method, 'DELETE')
    return Response.json({ ok: true, revoked: 1 })
  }

  await permissions.revokePermissionGrant(permissions.permissionGrants.value[0])

  assert.equal(deletedUrl, '/api/sessions/session-grant/grants/grant-file')
  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/work'])
})

test('新任务请求不再携带全局审批模式和目录范围', async () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'auto_review', version: 2,
    workspace_root: '/srv/work', auto_review_roots: ['/srv/long-task'],
  })
  assert.deepEqual(permissions.permissionRequestPayload(), {
    workspace_root: '/srv/work',
  })
})

test('移除一个自动执行目录时保留审批模式和其他范围', async () => {
  reset()
  permissions.bindPermissionSession('session-roots')
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', version: 4,
    auto_review_roots: ['/srv/a', '/srv/b'],
  })
  let submitted
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/permissions')
    submitted = JSON.parse(options.body)
    return Response.json({
      mode: 'ask', version: 5,
      auto_review_roots: ['/srv/b'],
    })
  }

  await permissions.revokeAutoReviewRoot('/srv/a')

  assert.deepEqual(submitted.auto_review_roots, ['/srv/b'])
  assert.equal('ttl_seconds' in submitted, false)
  assert.deepEqual(permissions.autoReviewRoots.value, ['/srv/b'])
})
