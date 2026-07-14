import assert from 'node:assert/strict'
import test from 'node:test'

let fixture = null
const requests = []
globalThis.fetch = async (url, options = {}) => {
  requests.push({ url, options })
  if (url === '/api/extensions' && !options.method) return Response.json(fixture)
  return Response.json({ ok: true })
}

const extensions = await import('../src/composables/useExtensions.js')

function baseFixture() {
  return {
    enabled_mcp_servers: [{
      id: 'sysinfo', name: '系统状态', source: 'builtin',
      tool_count: 4, available: true,
    }],
    mcp_servers: [{
      id: 'files', name: 'Files', command: '/usr/bin/files-mcp', enabled: false,
      secret_env_keys: ['TOKEN'], tool_count: 2,
    }],
    skills: [{
      id: 'review', name: 'Review', enabled: true, source: 'builtin',
      required_tools: ['files.read'],
      sha256: 'a'.repeat(64), available: true,
    }],
    skill_issues: [{
      id: 'broken', source: 'user', message: 'SKILL.md frontmatter 无效',
    }],
  }
}

function reset() {
  extensions._resetExtensionStateForTests()
  fixture = baseFixture()
  requests.length = 0
}

test('读取扩展并维护仅限已启用 Skill 的草稿选择', async () => {
  reset()
  const firstLoad = extensions.loadExtensions()
  const concurrentLoad = extensions.loadExtensions()
  assert.equal(firstLoad, concurrentLoad)
  await firstLoad
  assert.equal(requests.filter((request) => request.url === '/api/extensions').length, 1)
  assert.equal(extensions.mcpServers.value[0].secretEnvKeys[0], 'TOKEN')
  assert.equal(extensions.enabledMcpServers.value[0].name, '系统状态')
  assert.equal(extensions.enabledMcpServers.value[0].source, 'builtin')
  assert.equal(extensions.enabledSkills.value.length, 1)
  assert.equal(extensions.extensionSkillIssues.value[0].id, 'broken')

  extensions.setSelectedSkill('review')
  assert.equal(extensions.selectedSkillId.value, 'review')

  fixture.skills[0].enabled = false
  await extensions.loadExtensions()
  assert.equal(extensions.selectedSkillId.value, '')
})

test('MCP 与 Skill 启停使用独立 enabled 端点并刷新列表', async () => {
  reset()
  await extensions.loadExtensions()
  requests.length = 0

  await extensions.setMcpServerEnabled('files', true, 3)
  assert.equal(requests[0].url, '/api/extensions/mcp/files/enabled')
  assert.deepEqual(JSON.parse(requests[0].options.body), { enabled: true, version: 3 })
  assert.equal(requests.at(-1).url, '/api/extensions')

  requests.length = 0
  await extensions.setSkillEnabled('review', false, 'a'.repeat(64), true)
  assert.equal(requests[0].url, '/api/extensions/skills/review/enabled')
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    enabled: false,
    expected_sha256: 'a'.repeat(64),
    expected_enabled: true,
  })
})
