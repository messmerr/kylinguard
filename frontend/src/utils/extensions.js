function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function textList(value) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function normalizeTool(raw) {
  if (typeof raw === 'string') return { name: raw, description: '' }
  return {
    name: text(raw?.name || raw?.id),
    description: text(raw?.description),
    inputSchema: raw?.input_schema && typeof raw.input_schema === 'object'
      ? raw.input_schema
      : raw?.inputSchema && typeof raw.inputSchema === 'object'
        ? raw.inputSchema : {},
  }
}

export function normalizeMcpServer(raw = {}) {
  const tools = Array.isArray(raw.tools)
    ? raw.tools.map(normalizeTool).filter((tool) => tool.name)
    : []
  const env = raw.env && typeof raw.env === 'object' && !Array.isArray(raw.env)
    ? Object.fromEntries(Object.entries(raw.env).map(([key, value]) => [key, String(value ?? '')]))
    : {}
  return {
    id: text(raw.id),
    name: text(raw.name) || text(raw.id) || '未命名 MCP 服务',
    command: text(raw.command),
    cwd: text(raw.cwd),
    args: textList(raw.args),
    env,
    secretEnvKeys: textList(raw.secret_env_keys ?? raw.secretEnvKeys),
    enabled: raw.enabled !== false,
    status: text(raw.status) || 'unknown',
    toolCount: Number.isFinite(Number(raw.tool_count ?? raw.toolCount))
      ? Number(raw.tool_count ?? raw.toolCount) : tools.length,
    tools,
    error: text(raw.error),
    version: Number(raw.version) || 0,
    source: text(raw.source),
    available: raw.available !== false,
  }
}

export function normalizeSkill(raw = {}) {
  const missingTools = textList(raw.missing_tools ?? raw.missingTools)
  return {
    id: text(raw.id),
    name: text(raw.name) || text(raw.id) || '未命名 Skill',
    description: text(raw.description),
    version: text(raw.version),
    enabled: raw.enabled !== false,
    requiredTools: textList(raw.required_tools ?? raw.requiredTools),
    missingTools,
    available: raw.available === undefined
      ? missingTools.length === 0
      : Boolean(raw.available),
    instructions: typeof raw.instructions === 'string' ? raw.instructions : '',
    source: text(raw.source),
    sha256: text(raw.sha256),
  }
}

export function formatArgs(args) {
  return textList(args).join('\n')
}

export function parseArgs(value) {
  return String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

export function formatEnv(env) {
  if (!env || typeof env !== 'object') return ''
  return Object.entries(env).map(([key, value]) => `${key}=${value ?? ''}`).join('\n')
}

function parseEnvLines(value, { allowBlank = false } = {}) {
  const result = {}
  const lines = String(value || '').split(/\r?\n/)
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    if (!line.trim()) continue
    const separator = line.indexOf('=')
    const key = separator < 0 ? '' : line.slice(0, separator).trim()
    if (!/^[A-Z_][A-Z0-9_]{0,127}$/.test(key)) {
      throw new Error(`环境变量第 ${index + 1} 行格式无效，请使用 KEY=value`)
    }
    const envValue = line.slice(separator + 1)
    if (!envValue && !allowBlank) throw new Error(`环境变量 ${key} 的值不能为空`)
    result[key] = envValue
  }
  return result
}

export function parseEnv(value) {
  return parseEnvLines(value)
}

export function parseSecretEnv(value) {
  return Object.fromEntries(
    Object.entries(parseEnvLines(value, { allowBlank: true }))
      .filter(([, secret]) => Boolean(secret)),
  )
}

export function formatList(values) {
  return textList(values).join('\n')
}

export function parseList(value) {
  return [...new Set(String(value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean))]
}

function slug(value, fallback = '') {
  const normalized = String(value || '').trim().toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '')
    .slice(0, 64)
  return normalized || fallback
}

const BUILTIN_MCP_IDS = new Set([
  'sysinfo', 'services', 'logs', 'network', 'disk', 'security',
  'run_command', 'files',
])

function shellWords(commandLine) {
  const input = String(commandLine || '').trim()
  if (!input) throw new Error('请粘贴 MCP JSON 配置或完整 stdio 命令')
  const words = []
  let word = ''
  let quote = ''
  let active = false
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index]
    if (quote) {
      if (character === quote) {
        quote = ''
      } else if (character === '\\' && quote === '"') {
        index += 1
        if (index >= input.length) throw new Error('命令末尾的转义符不完整')
        word += input[index]
      } else {
        word += character
      }
      active = true
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      active = true
    } else if ('|&;<>'.includes(character)) {
      throw new Error(`完整命令不支持未加引号的 shell 操作符 ${character}；请只填写直接启动 stdio 程序的命令`)
    } else if (/\s/.test(character)) {
      if (active) {
        words.push(word)
        word = ''
        active = false
      }
    } else if (character === '\\') {
      index += 1
      if (index >= input.length) throw new Error('命令末尾的转义符不完整')
      word += input[index]
      active = true
    } else {
      word += character
      active = true
    }
  }
  if (quote) throw new Error('命令中的引号没有闭合')
  if (active) words.push(word)
  return words
}

const SECRET_ENV_NAME = /(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIALS?|PRIVATE_?KEY|AUTH|AUTHORIZATION|COOKIE)(?:$|_)/i
const ENV_NAME = /^[A-Z_][A-Z0-9_]{0,127}$/
const ENV_PLACEHOLDER = /^\$\{[^}\r\n]+\}$/

function mcpId(value) {
  let normalized = slug(value, 'mcp-server').replace(/\.+/g, '-')
  if (!/^[a-z]/.test(normalized)) normalized = `mcp-${normalized}`.slice(0, 64)
  if (BUILTIN_MCP_IDS.has(normalized)) normalized = `custom-${normalized}`
  return normalized.slice(0, 64)
}

function envObject(raw, label) {
  if (raw === undefined || raw === null) return {}
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error(`${label} 必须是 KEY=value 对象`)
  }
  const result = {}
  for (const [key, value] of Object.entries(raw)) {
    if (!ENV_NAME.test(key)) throw new Error(`${label} 包含无效变量名 ${key}`)
    if (!['string', 'number', 'boolean'].includes(typeof value)) {
      throw new Error(`${label} 中 ${key} 的值必须是字符串或简单值`)
    }
    result[key] = String(value)
  }
  return result
}

function splitImportedEnv(ordinaryRaw, secretRaw = {}) {
  const ordinary = envObject(ordinaryRaw, '环境变量')
  const declaredSecret = envObject(secretRaw, '敏感环境变量')
  const env = {}
  const secretEnv = {}
  const warnings = []
  for (const [key, value] of Object.entries(ordinary)) {
    if (SECRET_ENV_NAME.test(key)) {
      secretEnv[key] = ENV_PLACEHOLDER.test(value) ? '' : value
      if (ENV_PLACEHOLDER.test(value)) warnings.push(`${key} 是变量占位符，请在敏感环境变量中补填真实值`)
    } else {
      env[key] = value
      if (ENV_PLACEHOLDER.test(value)) warnings.push(`${key} 是变量占位符，stdio 启动不会自动展开，请改为实际值`)
    }
  }
  for (const [key, value] of Object.entries(declaredSecret)) {
    secretEnv[key] = ENV_PLACEHOLDER.test(value) ? '' : value
    if (ENV_PLACEHOLDER.test(value)) warnings.push(`${key} 是变量占位符，请补填真实值`)
  }
  return { env, secretEnv, warnings }
}

function mcpObject(raw, sourceId = '') {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('单个 MCP 配置必须是 JSON 对象')
  }
  const transport = String(raw.type ?? raw.transport ?? '').trim().toLowerCase()
  if (raw.url || (transport && transport !== 'stdio')) {
    throw new Error('当前只支持本机 stdio MCP，暂不支持 HTTP、SSE 或远程 URL')
  }
  const command = typeof raw.command === 'string' ? raw.command.trim() : ''
  if (!command) throw new Error('MCP 配置缺少 command')
  if (raw.args !== undefined && !Array.isArray(raw.args)) throw new Error('MCP 配置的 args 必须是数组')
  if (Array.isArray(raw.args) && raw.args.some((item) => typeof item !== 'string')) {
    throw new Error('MCP 配置的 args 每一项都必须是字符串')
  }
  const args = Array.isArray(raw.args) ? [...raw.args] : []
  const importedEnv = splitImportedEnv(
    raw.env,
    raw.secret_env ?? raw.secretEnv,
  )
  const commandName = command.split('/').filter(Boolean).at(-1) || 'mcp-server'
  const rawName = text(raw.name) || sourceId || commandName
  const warnings = [...importedEnv.warnings]
  if (!command.startsWith('/')) {
    warnings.push(`启动命令“${command}”不是绝对路径；保存前请改为后端主机上的实际路径`)
  }
  const cwd = text(raw.cwd)
  if (cwd && !cwd.startsWith('/')) warnings.push('工作目录不是绝对路径；保存前请改为后端主机上的实际路径')
  return {
    id: mcpId(raw.id || sourceId || rawName),
    name: rawName,
    command,
    cwd,
    args,
    env: importedEnv.env,
    secretEnv: importedEnv.secretEnv,
    warnings,
  }
}

/**
 * 解析常见 mcpServers JSON、单个 MCP JSON 或一条 stdio 命令。
 * 该函数只做字符串解析，绝不调用 shell、解析 PATH 或访问网络。
 */
export function parseMcpImport(value, selectedServerId = '') {
  const input = String(value || '').trim()
  if (!input) throw new Error('请粘贴 MCP JSON 配置或完整 stdio 命令')
  if (input.startsWith('{') || input.startsWith('[')) {
    let parsed
    try {
      parsed = JSON.parse(input)
    } catch {
      throw new Error('JSON 格式无效，请检查括号、引号和末尾逗号')
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('MCP JSON 顶层必须是对象')
    }
    if (Object.hasOwn(parsed, 'mcpServers')) {
      const servers = parsed.mcpServers
      if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
        throw new Error('mcpServers 必须是对象')
      }
      const entries = Object.entries(servers)
      if (!entries.length) throw new Error('mcpServers 中没有服务')
      if (entries.length > 1) {
        if (!selectedServerId) {
          return {
            choices: entries.map(([id, server]) => ({
              id,
              name: text(server?.name) || id,
            })),
          }
        }
        const selected = entries.find(([id]) => id === selectedServerId)
        if (!selected) throw new Error('所选 MCP 服务已不在当前配置中，请重新选择')
        return mcpObject(selected[1], selected[0])
      }
      return mcpObject(entries[0][1], entries[0][0])
    }
    return mcpObject(parsed)
  }

  const words = shellWords(input)
  const leadingEnv = {}
  while (words.length && /^[A-Z_][A-Z0-9_]{0,127}=/.test(words[0])) {
    const assignment = words.shift()
    const separator = assignment.indexOf('=')
    leadingEnv[assignment.slice(0, separator)] = assignment.slice(separator + 1)
  }
  const command = words.shift()
  if (!command) throw new Error('命令中缺少可执行程序')
  return mcpObject({ command, args: words, env: leadingEnv })
}

function frontmatterScalar(raw) {
  const value = String(raw || '').trim()
  if (!value) return ''
  if (value === 'true' || value === 'false') return value === 'true'
  if (value.startsWith('[')) {
    if (!value.endsWith(']')) throw new Error('SKILL.md 的行内列表缺少右方括号')
    try {
      const parsed = JSON.parse(value)
      if (!Array.isArray(parsed)) throw new Error()
      return parsed.map((item) => String(item))
    } catch {
      return value.slice(1, -1).split(',')
        .map((item) => item.trim().replace(/^(['"])(.*)\1$/, '$2'))
        .filter(Boolean)
    }
  }
  if (value.startsWith('"')) {
    if (!value.endsWith('"')) throw new Error('SKILL.md 的双引号字符串没有闭合')
    try {
      const parsed = JSON.parse(value)
      if (typeof parsed !== 'string') throw new Error()
      return parsed
    } catch {
      throw new Error('SKILL.md 的双引号字符串转义无效')
    }
  }
  if (value.startsWith("'")) {
    if (!value.endsWith("'")) throw new Error('SKILL.md 的单引号字符串没有闭合')
    return value.slice(1, -1)
  }
  return value
}

function parseSkillFrontmatter(header) {
  const lines = header.split(/\r?\n/)
  const result = {}
  const listFields = new Set(['required_tools', 'allowed_tools'])
  for (let index = 0; index < lines.length;) {
    const line = lines[index]
    if (!line.trim() || line.trimStart().startsWith('#')) {
      index += 1
      continue
    }
    if (/^\s/.test(line) || !line.includes(':')) {
      throw new Error(`SKILL.md frontmatter 第 ${index + 1} 行格式无效`)
    }
    const separator = line.indexOf(':')
    const key = line.slice(0, separator).trim()
    const raw = line.slice(separator + 1).trim()
    // 第三方 Skill 常带 allowed-tools、argument-hint、metadata 等扩展字段。
    // 导入只提取麒盾认识的字段，未知字段不会被保存或执行。
    if (!/^[a-z_][a-z0-9_-]*$/.test(key)) throw new Error(`SKILL.md 包含无效字段名 ${key}`)
    if (Object.hasOwn(result, key)) throw new Error(`SKILL.md 字段 ${key} 重复`)
    if (!raw) {
      if (!listFields.has(key)) {
        index += 1
        while (index < lines.length && (!lines[index].trim() || /^\s/.test(lines[index]))) index += 1
        result[key] = ''
        continue
      }
      const items = []
      index += 1
      while (index < lines.length) {
        if (!lines[index].trim()) {
          index += 1
          continue
        }
        const match = lines[index].match(/^\s+-\s+(.+)$/)
        if (!match) break
        items.push(String(frontmatterScalar(match[1])))
        index += 1
      }
      result[key] = items
      continue
    }
    if (/^[|>][-+]?$/.test(raw)) {
      const folded = raw.startsWith('>')
      const block = []
      index += 1
      while (index < lines.length && (!lines[index] || /^\s/.test(lines[index]))) {
        block.push(lines[index].replace(/^ {1,2}/, ''))
        index += 1
      }
      result[key] = folded ? block.join(' ').trim() : block.join('\n').trim()
      continue
    }
    result[key] = frontmatterScalar(raw)
    index += 1
  }
  return result
}

/** 解析单个 SKILL.md；只读取受限 frontmatter 与正文，不加载脚本或资源。 */
export function parseSkillImport(value) {
  const input = String(value || '').replace(/^\uFEFF/, '')
  if (new TextEncoder().encode(input).length > 128 * 1024) throw new Error('SKILL.md 不能超过 128 KiB')
  const lines = input.split(/\r?\n/)
  if (lines[0]?.trim() !== '---') throw new Error('SKILL.md 必须以 YAML frontmatter（---）开头')
  const closing = lines.findIndex((line, index) => index > 0 && line.trim() === '---')
  if (closing < 0) throw new Error('SKILL.md 的 frontmatter 缺少结束分隔符 ---')
  const metadata = parseSkillFrontmatter(lines.slice(1, closing).join('\n'))
  const instructions = lines.slice(closing + 1).join('\n').trim()
  const name = typeof metadata.name === 'string' ? metadata.name.trim() : ''
  if (!name) throw new Error('SKILL.md 缺少非空 name')
  const description = typeof metadata.description === 'string' ? metadata.description.trim() : ''
  if (!description) throw new Error('SKILL.md 缺少非空 description')
  if (!instructions) throw new Error('SKILL.md 缺少工作流正文')
  const requiredTools = Array.isArray(metadata.required_tools) ? metadata.required_tools : []
  if (metadata.required_tools !== undefined && !Array.isArray(metadata.required_tools)) {
    throw new Error('required_tools 必须是列表')
  }
  if (description.length > 1024) throw new Error('SKILL.md 的 description 不能超过 1024 个字符')
  const supportedFields = new Set([
    'id', 'name', 'description', 'version', 'required_tools',
    'enabled',
  ])
  const legacyScopeFields = ['allowed_tools', 'allow_all_tools', 'allowed-tools']
    .filter((key) => Object.hasOwn(metadata, key))
  const ignoredFields = Object.keys(metadata).filter((key) => (
    !supportedFields.has(key) && !legacyScopeFields.includes(key)
  ))
  const warnings = []
  if (legacyScopeFields.length) {
    warnings.push(
      `已忽略工具范围字段：${legacyScopeFields.join('、')}。Skill 只提供工作方法，不授予或限制工具权限。`,
    )
  }
  if (ignoredFields.length) {
    warnings.push(`未保存平台专用元数据：${ignoredFields.join('、')}；Skill 正文仍会正常导入。`)
  }
  return {
    id: slug(metadata.id || name),
    name,
    description,
    version: typeof metadata.version === 'string' ? metadata.version.trim() : '1.0.0',
    requiredTools: [...new Set(requiredTools.map((item) => String(item).trim()).filter(Boolean))],
    instructions,
    enabled: false,
    warnings,
  }
}

export function mcpFormPayload(form, { editing = false } = {}) {
  const id = text(form.id)
  const name = text(form.name)
  const command = text(form.command)
  if (!editing && !/^[a-z][a-z0-9_-]{0,63}$/.test(id)) {
    throw new Error('MCP 标识须以小写字母开头，且只包含小写字母、数字、下划线或短横线')
  }
  if (!editing && BUILTIN_MCP_IDS.has(id)) {
    throw new Error('MCP 标识与内置服务冲突，请换一个标识')
  }
  if (!name) throw new Error('请填写服务名称')
  if (!command) throw new Error('请填写启动命令')
  if (!command.startsWith('/')) throw new Error('启动命令必须使用后端主机上的绝对路径')
  const cwd = text(form.cwd)
  if (cwd && !cwd.startsWith('/')) throw new Error('工作目录必须使用后端主机上的绝对路径')
  const secretEnv = parseSecretEnv(form.secretEnvText)
  const clearSecretEnvKeys = textList(form.clearSecretEnvKeys)
  const conflict = clearSecretEnvKeys.find((key) => secretEnv[key])
  if (conflict) throw new Error(`敏感环境变量 ${conflict} 不能同时更新和移除`)
  return {
    ...(!editing ? { id } : {}),
    name,
    command,
    ...(cwd ? { cwd } : {}),
    args: parseArgs(form.argsText),
    env: parseEnv(form.envText),
    ...(Object.keys(secretEnv).length ? { secret_env: secretEnv } : {}),
    ...(editing && clearSecretEnvKeys.length
      ? { clear_secret_env_keys: clearSecretEnvKeys } : {}),
    enabled: false,
    ...(editing && Number(form.version) > 0 ? { version: Number(form.version) } : {}),
  }
}

export function skillFormPayload(form, { editing = false } = {}) {
  const id = text(form.id)
  const name = text(form.name)
  if (!editing && !/^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/.test(id)) {
    throw new Error('Skill 标识仅允许小写字母、数字、点、下划线和短横线，且须以字母或数字开头、结尾')
  }
  if (!name) throw new Error('请填写 Skill 名称')
  if (!String(form.instructions || '').trim()) throw new Error('请填写 Skill 指令')
  const description = String(form.description || '').trim()
  if (description.length > 1024) throw new Error('Skill 说明不能超过 1024 个字符')
  const requiredTools = parseList(form.requiredToolsText)
  return {
    ...(!editing ? { id } : {}),
    name,
    description,
    version: text(form.version),
    enabled: form.enabled !== false,
    required_tools: requiredTools,
    ...(editing ? { expected_sha256: text(form.expectedSha256) } : {}),
    instructions: String(form.instructions || '').trim(),
  }
}

export function mcpStatus(server) {
  if (!server.enabled) return { label: '未启用', tone: 'muted' }
  const status = String(server.status || '').toLowerCase()
  if (['ok', 'ready', 'running', 'healthy', 'connected'].includes(status)) {
    return { label: '可用', tone: 'ok' }
  }
  if (['error', 'failed', 'unhealthy', 'unavailable'].includes(status) || server.error) {
    return { label: '异常', tone: 'failed' }
  }
  if (['testing', 'starting', 'loading'].includes(status)) {
    return { label: '检查中', tone: 'pending' }
  }
  return { label: '未测试', tone: 'unknown' }
}
