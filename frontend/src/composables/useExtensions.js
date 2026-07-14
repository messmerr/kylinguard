import { computed, reactive, ref } from 'vue'
import { apiFetch } from './useApi.js'
import { normalizeMcpServer, normalizeSkill } from '../utils/extensions.js'

export const mcpServers = ref([])
export const enabledMcpServers = ref([])
export const extensionSkills = ref([])
export const extensionSkillIssues = ref([])
export const extensionsLoading = ref(false)
export const extensionError = ref('')
export const extensionActions = reactive(new Set())
export const selectedSkillId = ref('')
export const enabledSkills = computed(() => extensionSkills.value.filter((skill) => skill.enabled))
let extensionsLoadPromise = null

function detailMessage(body, fallback) {
  if (typeof body?.detail === 'string') return body.detail
  if (typeof body?.detail?.message === 'string') return body.detail.message
  if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === 'string') {
    return body.detail[0].msg
  }
  if (typeof body?.message === 'string') return body.message
  return fallback
}

async function readJson(response) {
  return response.json().catch(() => ({}))
}

function applyExtensions(raw = {}) {
  if (Array.isArray(raw.mcp_servers)) {
    mcpServers.value = raw.mcp_servers.map(normalizeMcpServer).filter((item) => item.id)
  }
  if (Array.isArray(raw.enabled_mcp_servers)) {
    enabledMcpServers.value = raw.enabled_mcp_servers
      .map(normalizeMcpServer).filter((item) => item.id)
  }
  if (Array.isArray(raw.skills)) {
    extensionSkills.value = raw.skills.map(normalizeSkill).filter((item) => item.id)
    if (selectedSkillId.value
        && !extensionSkills.value.some((skill) => (
          skill.id === selectedSkillId.value && skill.enabled && skill.available
        ))) {
      setSelectedSkill('')
    }
  }
  extensionSkillIssues.value = Array.isArray(raw.skill_issues)
    ? raw.skill_issues.map((issue) => ({
      id: String(issue?.id || ''),
      source: String(issue?.source || ''),
      message: String(issue?.message || '无法加载 Skill'),
    }))
    : []
  return raw
}

async function request(url, options, fallback) {
  const response = await apiFetch(url, options)
  const body = await readJson(response)
  if (!response.ok) {
    const error = new Error(detailMessage(body, `${fallback}（HTTP ${response.status}）`))
    error.status = response.status
    throw error
  }
  return body
}

async function runAction(key, action) {
  if (extensionActions.has(key)) return null
  extensionActions.add(key)
  extensionError.value = ''
  try {
    return await action()
  } catch (error) {
    extensionError.value = error.message || '扩展操作失败'
    if (error.status === 409) await refreshAfterMutation()
    throw error
  } finally {
    extensionActions.delete(key)
  }
}

async function refreshAfterMutation() {
  if (extensionsLoadPromise) await extensionsLoadPromise.catch(() => {})
  await loadExtensions().catch(() => {})
}

export function extensionActionBusy(kind, id) {
  return extensionActions.has(`${kind}:${id}`)
}

export function loadExtensions() {
  if (extensionsLoadPromise) return extensionsLoadPromise
  extensionsLoading.value = true
  extensionError.value = ''
  extensionsLoadPromise = request('/api/extensions', {}, '扩展配置读取失败')
    .then(applyExtensions)
    .catch((error) => {
      extensionError.value = error.message || '扩展配置读取失败'
      throw error
    })
    .finally(() => {
      extensionsLoading.value = false
      extensionsLoadPromise = null
    })
  return extensionsLoadPromise
}

export function createMcpServer(payload) {
  return runAction('mcp:create', async () => {
    const body = await request('/api/extensions/mcp', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }, 'MCP 服务添加失败')
    await refreshAfterMutation()
    return body
  })
}

export function updateMcpServer(id, payload) {
  const encoded = encodeURIComponent(id)
  return runAction(`mcp:update:${id}`, async () => {
    const body = await request(`/api/extensions/mcp/${encoded}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }, 'MCP 服务保存失败')
    await refreshAfterMutation()
    return body
  })
}

export function deleteMcpServer(id, version = 0) {
  const encoded = encodeURIComponent(id)
  return runAction(`mcp:delete:${id}`, async () => {
    const body = await request(`/api/extensions/mcp/${encoded}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      ...(version > 0 ? { body: JSON.stringify({ version }) } : {}),
    }, 'MCP 服务删除失败')
    await refreshAfterMutation()
    return body
  })
}

export function testMcpServer(id, version = 0) {
  const encoded = encodeURIComponent(id)
  return runAction(`mcp:test:${id}`, async () => {
    const body = await request(`/api/extensions/mcp/${encoded}/test`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      ...(version > 0 ? { body: JSON.stringify({ version }) } : {}),
    }, 'MCP 连接测试失败')
    await refreshAfterMutation()
    return body
  })
}

export function setMcpServerEnabled(id, enabled, version = 0) {
  const encoded = encodeURIComponent(id)
  return runAction(`mcp:enabled:${id}`, async () => {
    const body = await request(`/api/extensions/mcp/${encoded}/enabled`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: Boolean(enabled),
        ...(version > 0 ? { version } : {}),
      }),
    }, 'MCP 服务状态修改失败')
    await refreshAfterMutation()
    return body
  })
}

export function setSkillEnabled(id, enabled, expectedSha256, expectedEnabled) {
  const encoded = encodeURIComponent(id)
  return runAction(`skill:enabled:${id}`, async () => {
    const body = await request(`/api/extensions/skills/${encoded}/enabled`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: Boolean(enabled),
        expected_sha256: String(expectedSha256 || ''),
        expected_enabled: Boolean(expectedEnabled),
      }),
    }, 'Skill 状态修改失败')
    await refreshAfterMutation()
    return body
  })
}

export function createSkill(payload) {
  return runAction('skill:create', async () => {
    const body = await request('/api/extensions/skills', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }, 'Skill 添加失败')
    await refreshAfterMutation()
    return body
  })
}

export function updateSkill(id, payload) {
  const encoded = encodeURIComponent(id)
  return runAction(`skill:update:${id}`, async () => {
    const body = await request(`/api/extensions/skills/${encoded}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }, 'Skill 保存失败')
    await refreshAfterMutation()
    return body
  })
}

export function deleteSkill(id, expectedSha256, expectedEnabled) {
  const encoded = encodeURIComponent(id)
  return runAction(`skill:delete:${id}`, async () => {
    const body = await request(`/api/extensions/skills/${encoded}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_sha256: String(expectedSha256 || ''),
        expected_enabled: Boolean(expectedEnabled),
      }),
    }, 'Skill 删除失败')
    await refreshAfterMutation()
    return body
  })
}

export function setSelectedSkill(id = '') {
  const next = String(id || '').trim()
  if (next && !enabledSkills.value.some((skill) => skill.id === next && skill.available)) {
    throw new Error('所选 Skill 当前不可用')
  }
  selectedSkillId.value = next
  return next
}

export function _resetExtensionStateForTests() {
  mcpServers.value = []
  enabledMcpServers.value = []
  extensionSkills.value = []
  extensionSkillIssues.value = []
  extensionsLoading.value = false
  extensionError.value = ''
  extensionActions.clear()
  selectedSkillId.value = ''
}
