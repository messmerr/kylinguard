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
const chatViewSource = await readFile(
  new URL('../src/views/ChatView.vue', import.meta.url), 'utf8',
)

function reset() {
  permissions._resetPermissionStateForTests()
  fetchImpl = async (url) => { throw new Error(`unexpected fetch: ${url}`) }
}

test('新会话固定回到确认后执行，并收回已有授权', () => {
  reset()
  permissions.permissionContext.mode = 'full_access'
  permissions.recordLocalGrant({
    id: 'session-root', path: '/srv/session', actions: ['create'], lifetime: 'session',
  })
  permissions.beginNewPermissionSession()

  assert.equal(permissions.permissionMode.value, 'ask')
  assert.equal(permissions.permissionContext.sessionId, '')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'unknown')
  assert.deepEqual(permissions.trustedRoots.value, [])
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

test('两个权限入口复用首条消息前的会话权限编排', () => {
  assert.match(permissionSelectorSource, /setChatPermissionMode\(mode/)
  assert.match(policyViewSource, /setChatPermissionMode\(mode/)
  assert.doesNotMatch(permissionSelectorSource, /发送第一条消息后可开启/)
  assert.doesNotMatch(policyViewSource, /发送第一条消息创建任务后/)
})

test('新任务 composer 明确选择服务器工作目录且已有任务锁定', () => {
  assert.match(chatViewSource, /设置服务器工作目录/)
  assert.match(chatViewSource, /不是浏览器本地文件夹，也不是安全沙箱/)
  assert.match(chatViewSource, /:disabled="running \|\| Boolean\(activeId\)"/)
  assert.match(chatViewSource, /setDraftWorkspaceRoot/)
})

test('可信目录规范化去重并进入首轮 chat 权限载荷', async () => {
  reset()
  await permissions.addTrustedRoot('/srv/demo/../docs/')
  await permissions.addTrustedRoot('/srv/docs')
  await permissions.setPermissionMode('trusted_workspace')

  assert.deepEqual(permissions.trustedRoots.value, ['/srv/docs'])
  assert.deepEqual(permissions.permissionRequestPayload(), {
    permission_mode: 'trusted_workspace',
    trusted_roots: ['/srv/docs'],
    permission_ttl_seconds: 1800,
  })
})

test('从会话权限接口读取模式、执行身份与有效授权', async () => {
  reset()
  fetchImpl = async (url) => {
    if (url === '/api/sessions/session-1/permissions') {
      return Response.json({
        mode: 'workspace', version: 3,
        execution_identity: 'backend-user', execution_identity_source: 'backend_process',
        workspace_root: '/srv/project', command_shell: '/bin/bash',
        command_max_timeout: 900, execution_account_separated: false,
        control_plane_isolated: true, grants_root: true,
        full_access_available: true,
        trusted_roots: ['/srv/project'],
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

  const result = await permissions.loadPermissionContext('session-1')

  assert.equal(result.supported, true)
  assert.equal(permissions.permissionMode.value, 'trusted_workspace')
  assert.equal(permissions.permissionContext.version, 3)
  assert.equal(permissions.permissionContext.executorIdentity, 'backend-user')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'backend_process')
  assert.equal(permissions.permissionContext.workspaceRoot, '/srv/project')
  assert.equal(permissions.permissionContext.commandShell, '/bin/bash')
  assert.equal(permissions.permissionContext.commandMaxTimeout, 900)
  assert.equal(permissions.permissionContext.executionAccountSeparated, false)
  assert.equal(permissions.permissionContext.grantsRoot, true)
  assert.equal(permissions.executionIdentitySourceLabel(), '后端当前 OS 身份')
  assert.deepEqual(permissions.trustedRoots.value, ['/srv/project'])
  assert.equal(permissions.permissionGrants.value.length, 1)
  assert.equal(permissions.permissionGrants.value[0].label, '/srv/project/report.md')
})

test('完全访问升级集中通过 permissions PUT 并携带时限', async () => {
  reset()
  permissions.bindPermissionSession('session-full')
  permissions.permissionContext.version = 1
  permissions.permissionContext.fullAccessMaxTtl = 600
  let submitted
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/sessions/session-full/permissions')
    assert.equal(options.method, 'PUT')
    submitted = JSON.parse(options.body)
    return Response.json({
      mode: 'full_access', version: 1,
      executor_identity: 'root', control_plane_isolated: true,
      grants_root: true, expires_at: 2_000_000_000,
    })
  }

  await permissions.setPermissionMode('full_access', { durationMinutes: 30 })

  assert.deepEqual(submitted, {
    mode: 'full_access', version: 1, trusted_roots: [],
    ttl_seconds: 600,
  })
  assert.equal(permissions.fullAccessActive.value, true)
  assert.equal(permissions.permissionContext.executorIdentity, 'root')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'legacy')
  assert.equal(permissions.executionIdentitySourceLabel(), '旧版 API（身份来源未说明）')
  assert.equal(permissions.permissionContext.executionAccountSeparated, true)
  assert.equal(permissions.permissionContext.grantsRoot, true)
  assert.equal(permissions.permissionContext.expiresAt, 2_000_000_000_000)
})

test('完全访问默认时长使用默认TTL而不是直接取最大TTL', () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context',
    mode: 'ask',
    permission_default_ttl: 15 * 60,
    full_access_max_ttl: 2 * 60 * 60,
  })

  assert.equal(permissions.fullAccessDurationMinutes.value, 15)
})

test('动作授权即使资源是路径也不会升级成可信目录', () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'ask', executor_user: 'ops', version: 2,
  })
  permissions.applyPermissionEvent({
    type: 'permission_grant_created',
    grant: { id: 'g2', path: '/var/reports', actions: ['write'], lifetime: 'session' },
  })
  assert.deepEqual(permissions.trustedRoots.value, [])
  assert.equal(permissions.permissionGrants.value.length, 1)

  permissions.applyPermissionEvent({ type: 'permission_revoked', grant_id: 'g2' })
  assert.deepEqual(permissions.trustedRoots.value, [])
  assert.equal(permissions.permissionGrants.value.length, 0)
  assert.equal(permissions.permissionContext.executorIdentity, 'ops')
  assert.equal(permissions.permissionContext.executorIdentitySource, 'legacy')
})

test('限时权限到期后在前端立即恢复为确认后执行', () => {
  reset()
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'full_access',
    expires_at: 1_000, version: 2,
  })
  assert.equal(permissions.expirePermissionContext(1_000_001), true)
  assert.equal(permissions.permissionMode.value, 'ask')
  assert.equal(permissions.fullAccessActive.value, false)
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
    { confirmId: 'confirm-1' }, 'trust_path', scope,
  )

  assert.equal(calls[0].url, '/api/permission-requests/request-1/resolve')
  assert.deepEqual(calls[0].body, {
    decision: 'allow_session', context_version: 2, trusted_path: '',
  })
  assert.equal(calls[1].url, '/api/confirm')
  assert.equal(calls[1].body.approved, true)
  assert.equal(calls[1].body.decision, 'trust_path')
  assert.deepEqual(permissions.trustedRoots.value, [])
})

test('撤销文件动作授权走 grant DELETE，不改可信目录上下文', async () => {
  reset()
  permissions.bindPermissionSession('session-grant')
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'trusted_workspace', version: 2,
    trusted_roots: ['/srv/work'], expires_at: Date.now() + 3_600_000,
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
  assert.deepEqual(permissions.trustedRoots.value, ['/srv/work'])
})

test('草稿可信目录的 12 小时选择随首轮 chat 提交', async () => {
  reset()
  await permissions.addTrustedRoot('/srv/long-task', { lifetime: 'extended' })
  await permissions.setPermissionMode('trusted_workspace', {
    trustedRoots: ['/srv/long-task'], ttlSeconds: 12 * 3600,
  })
  assert.deepEqual(permissions.permissionRequestPayload(), {
    permission_mode: 'trusted_workspace',
    trusted_roots: ['/srv/long-task'],
    permission_ttl_seconds: 12 * 3600,
  })
})

test('移除一个可信目录时保留剩余目录与原授权期限', async () => {
  reset()
  permissions.bindPermissionSession('session-roots')
  const expiresAt = Date.now() + 2 * 3600 * 1000
  permissions.applyPermissionEvent({
    type: 'permission_context', mode: 'trusted_workspace', version: 4,
    trusted_roots: ['/srv/a', '/srv/b'], expires_at: expiresAt,
  })
  let submitted
  fetchImpl = async (url, options = {}) => {
    assert.equal(url, '/api/sessions/session-roots/permissions')
    submitted = JSON.parse(options.body)
    return Response.json({
      mode: 'trusted_workspace', version: 5,
      trusted_roots: ['/srv/b'], expires_at: expiresAt,
    })
  }

  await permissions.revokeTrustedRoot('/srv/a')

  assert.deepEqual(submitted.trusted_roots, ['/srv/b'])
  assert.ok(submitted.ttl_seconds >= 7198 && submitted.ttl_seconds <= 7200)
  assert.deepEqual(permissions.trustedRoots.value, ['/srv/b'])
})
