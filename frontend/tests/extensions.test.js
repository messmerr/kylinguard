import assert from 'node:assert/strict'
import test from 'node:test'

import {
  mcpFormPayload,
  normalizeMcpServer,
  normalizeSkill,
  parseEnv,
  parseMcpImport,
  parseSkillImport,
  skillFormPayload,
} from '../src/utils/extensions.js'

test('MCP 配置规范化时只读取敏感变量名称', () => {
  const server = normalizeMcpServer({
    id: 'files', name: 'Files', command: '/usr/bin/files-mcp',
    cwd: '/workspace/tools',
    args: ['--stdio'], env: { LOG_LEVEL: 'info' },
    secret_env: { TOKEN: 'must-not-enter-state' }, secret_env_keys: ['TOKEN'],
    tools: [{ name: 'files.read', description: 'read files' }], enabled: false,
  })

  assert.deepEqual(server.secretEnvKeys, ['TOKEN'])
  assert.equal(JSON.stringify(server).includes('must-not-enter-state'), false)
  assert.equal(server.toolCount, 1)
  assert.equal(server.cwd, '/workspace/tools')
})

test('MCP 表单区分普通与敏感环境变量且新建固定停用', () => {
  const payload = mcpFormPayload({
    id: 'file-tools', name: 'Files', command: '/usr/bin/files-mcp',
    cwd: '/workspace/tools', argsText: '--stdio\n--safe', envText: 'LOG_LEVEL=info',
    secretEnvText: 'TOKEN=secret\nEMPTY=', enabled: true,
  })

  assert.deepEqual(payload, {
    id: 'file-tools', name: 'Files', command: '/usr/bin/files-mcp',
    cwd: '/workspace/tools',
    args: ['--stdio', '--safe'], env: { LOG_LEVEL: 'info' },
    secret_env: { TOKEN: 'secret' }, enabled: false,
  })
  assert.throws(() => parseEnv('BAD KEY=value'), /第 1 行格式无效/)
  assert.throws(() => mcpFormPayload({
    id: 'bad', name: 'Bad', command: 'python', argsText: '', envText: '', secretEnvText: '',
  }), /绝对路径/)
  assert.throws(() => mcpFormPayload({
    id: 'bad', name: 'Bad', command: '/usr/bin/python', cwd: 'relative',
    argsText: '', envText: '', secretEnvText: '',
  }), /工作目录.*绝对路径/)
})

test('MCP 导入支持 mcpServers JSON 并分离敏感变量', () => {
  const imported = parseMcpImport(JSON.stringify({
    mcpServers: {
      'log-tools': {
        command: '/opt/mcp/log-server',
        args: ['--stdio'],
        env: {
          LOG_LEVEL: 'info',
          API_TOKEN: '${API_TOKEN}',
          CLIENT_SECRET: 'actual-secret',
        },
      },
    },
  }))

  assert.equal(imported.id, 'log-tools')
  assert.equal(imported.command, '/opt/mcp/log-server')
  assert.deepEqual(imported.args, ['--stdio'])
  assert.deepEqual(imported.env, { LOG_LEVEL: 'info' })
  assert.deepEqual(imported.secretEnv, { API_TOKEN: '', CLIENT_SECRET: 'actual-secret' })
  assert.match(imported.warnings.join('\n'), /API_TOKEN.*占位符/)
})

test('MCP 导入让用户从多个服务中明确选择并拒绝远程传输', () => {
  const input = JSON.stringify({
    mcpServers: {
      first: { command: '/opt/first' },
      second: { command: '/opt/second' },
    },
  })
  const choice = parseMcpImport(input)
  assert.deepEqual(choice.choices, [
    { id: 'first', name: 'first' },
    { id: 'second', name: 'second' },
  ])
  assert.equal(parseMcpImport(input, 'second').command, '/opt/second')
  assert.throws(() => parseMcpImport(input, 'missing'), /已不在当前配置/)
  assert.throws(() => parseMcpImport(JSON.stringify({
    name: 'remote', type: 'http', url: 'https://example.test/mcp',
  })), /只支持本机 stdio/)
})

test('MCP 导入解析完整命令但不替用户解析相对可执行路径', () => {
  const absolute = parseMcpImport("API_KEY=secret /opt/mcp/server --label 'hello world'")
  assert.equal(absolute.command, '/opt/mcp/server')
  assert.deepEqual(absolute.args, ['--label', 'hello world'])
  assert.deepEqual(absolute.secretEnv, { API_KEY: 'secret' })
  assert.deepEqual(absolute.warnings, [])

  const relative = parseMcpImport('npx -y @example/mcp-server')
  assert.equal(relative.command, 'npx')
  assert.deepEqual(relative.args, ['-y', '@example/mcp-server'])
  assert.match(relative.warnings.join('\n'), /不是绝对路径/)
  assert.throws(() => parseMcpImport("/opt/server 'unfinished"), /引号没有闭合/)
  assert.throws(() => parseMcpImport('/opt/server --stdio && echo unsafe'), /shell 操作符/)

  const spacedPath = parseMcpImport(JSON.stringify({
    id: 'tools.example.com', command: '/opt/My Tools/server', args: [],
  }))
  assert.equal(spacedPath.id, 'tools-example-com')
  assert.equal(spacedPath.command, '/opt/My Tools/server')
  assert.deepEqual(spacedPath.args, [])
  assert.equal(parseMcpImport(JSON.stringify({ id: '123', command: '/opt/server' })).id, 'mcp-123')
  assert.equal(parseMcpImport(JSON.stringify({ id: 'files', command: '/opt/server' })).id, 'custom-files')
})

test('Skill 表单只提交可选工具依赖并默认停用', () => {
  const payload = skillFormPayload({
    id: 'log-review', name: '日志排查', description: '检查日志', version: '1.0.0',
    requiredToolsText: 'logs.read, files.read\nlogs.read',
    instructions: '先读取日志，再给出结论。', enabled: false,
  })
  assert.deepEqual(payload.required_tools, ['logs.read', 'files.read'])
  assert.equal(Object.hasOwn(payload, 'allowed_tools'), false)
  assert.equal(Object.hasOwn(payload, 'allow_all_tools'), false)
  assert.equal(Object.hasOwn(payload, 'manual_only'), false)
  assert.equal(payload.enabled, false)

  const normalized = normalizeSkill({
    id: 'log-review', required_tools: ['logs.read'],
    allowed_tools: ['logs.read'], missing_tools: ['logs.read'], available: false,
    allow_all_tools: true,
    instructions: 'inspect', source: 'user',
  })
  assert.deepEqual(normalized.missingTools, ['logs.read'])
  assert.equal(normalized.available, false)
  assert.equal(Object.hasOwn(normalized, 'allowedTools'), false)
  assert.equal(Object.hasOwn(normalized, 'allowAllTools'), false)

  const dependencyOnly = skillFormPayload({
    id: 'dependency-only', name: 'Dependency only', instructions: 'Inspect',
    requiredToolsText: 'logs.read',
  })
  assert.deepEqual(dependencyOnly.required_tools, ['logs.read'])
  const instructionOnly = skillFormPayload({
    id: 'instruction-only', name: 'Instruction only', instructions: 'Inspect',
  })
  assert.deepEqual(instructionOnly.required_tools, [])

  const editing = skillFormPayload({
    id: 'dependency-only', name: 'Dependency only', instructions: 'Inspect',
    requiredToolsText: 'logs.read',
    expectedSha256: 'a'.repeat(64),
  }, { editing: true })
  assert.equal(editing.expected_sha256, 'a'.repeat(64))

  assert.throws(() => skillFormPayload({
    id: 'long-description', name: 'Long description', instructions: 'Inspect',
    description: 'a'.repeat(1025),
  }), /说明不能超过 1024/)
  assert.throws(() => mcpFormPayload({
    id: 'files', name: 'Reserved', command: '/opt/server',
  }), /与内置服务冲突/)
})

test('SKILL.md 导入解析受限 frontmatter 并始终生成停用草稿', () => {
  const imported = parseSkillImport(`---
name: log-review
description: >
  检查近期错误日志
  并给出处理建议
version: 2.0.0
required_tools:
  - logs.recent
allowed_tools: ["logs.recent", "files.read"]
allow_all_tools: false
enabled: true
---

先读取日志，再总结异常。`)

  assert.equal(imported.id, 'log-review')
  assert.equal(imported.name, 'log-review')
  assert.equal(imported.description, '检查近期错误日志 并给出处理建议')
  assert.equal(imported.version, '2.0.0')
  assert.deepEqual(imported.requiredTools, ['logs.recent'])
  assert.equal(Object.hasOwn(imported, 'allowedTools'), false)
  assert.equal(Object.hasOwn(imported, 'allowAllTools'), false)
  assert.equal(imported.enabled, false)
  assert.match(imported.warnings.join('\n'), /已忽略工具范围字段.*allowed_tools.*allow_all_tools/)
  assert.match(imported.warnings.join('\n'), /不授予或限制工具权限/)
  assert.equal(imported.instructions, '先读取日志，再总结异常。')
})

test('普通 Codex 与 Claude SKILL.md 导入不会被转换为工具权限', () => {
  const imported = parseSkillImport(`---
name: writing-guide
description: 只提供写作方法
allowed-tools: Read, Grep
argument-hint: topic
metadata:
  author: upstream
  category: writing
---
按结构给出建议，不调用工具。`)
  assert.deepEqual(imported.requiredTools, [])
  assert.equal(Object.hasOwn(imported, 'allowedTools'), false)
  assert.equal(Object.hasOwn(imported, 'allowAllTools'), false)
  assert.equal(imported.enabled, false)
  assert.match(imported.warnings.join('\n'), /已忽略工具范围字段.*allowed-tools/)
  assert.match(imported.warnings.join('\n'), /平台专用元数据.*argument-hint.*metadata/)

  assert.throws(() => parseSkillImport('name: missing-frontmatter'), /必须以 YAML frontmatter/)
  const legacyScope = parseSkillImport(`---
name: broken
description: broken skill
allow_all_tools: yes
allowed_tools: [logs.recent]
---
Do work.`)
  assert.match(legacyScope.warnings.join('\n'), /allowed_tools.*allow_all_tools/)
  assert.equal(Object.hasOwn(legacyScope, 'allowAllTools'), false)

  const escaped = parseSkillImport(`---
name: "A \\"quoted\\" Skill"
description: "Path C:\\\\Tools\\\\server"
---
Follow the quoted path.`)
  assert.equal(escaped.name, 'A "quoted" Skill')
  assert.equal(escaped.description, 'Path C:\\Tools\\server')

  assert.throws(() => parseSkillImport(`---
name: long-description
description: ${'a'.repeat(1025)}
---
Do work.`), /description 不能超过 1024/)
})
