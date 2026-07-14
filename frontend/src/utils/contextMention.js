function clean(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function fileName(path) {
  return String(path || '').split('/').filter(Boolean).at(-1) || String(path || '')
}

function codePoints(value) {
  return Array.from(String(value || ''))
}

function codePointLength(value) {
  return codePoints(value).length
}

function mentionLabel(node) {
  if (node?.type === 'skill') return clean(node.label) || clean(node.name) || clean(node.id)
  if (node?.type === 'file') {
    return clean(node.label) || clean(node.name) || fileName(node.relativePath || node.path)
  }
  return ''
}

function renderedMention(node) {
  const label = mentionLabel(node)
  return label ? `@${label}` : ''
}

/** 返回光标前正在输入的 @ 查询；邮箱等单词内部的 @ 不会触发。 */
export function mentionAtCursor(value, cursor) {
  const text = String(value || '')
  const end = Math.max(0, Math.min(Number.isFinite(cursor) ? cursor : text.length, text.length))
  const before = text.slice(0, end)
  const match = before.match(/(^|[\s\p{P}])@([^\s@]*)$/u)
  if (!match) return null
  const query = match[2]
  return { start: end - query.length - 1, end, query }
}

/** 只移除作为 UI 触发器的 @query，不改动正文其余字符和换行。 */
export function removeMention(value, mention) {
  const text = String(value || '')
  if (!mention || mention.start < 0 || mention.end < mention.start) {
    return { text, cursor: text.length }
  }
  const start = Math.min(mention.start, text.length)
  const end = Math.min(mention.end, text.length)
  return { text: text.slice(0, start) + text.slice(end), cursor: start }
}

export function filterMentionSkills(skills, query, limit = 12) {
  const needle = clean(query).toLocaleLowerCase()
  return (Array.isArray(skills) ? skills : [])
    .filter((skill) => skill?.enabled && skill?.available)
    .filter((skill) => !needle || [skill.name, skill.id, skill.description]
      .some((value) => String(value || '').toLocaleLowerCase().includes(needle)))
    .slice(0, limit)
}

export function normalizeContextFiles(files, limit = 8) {
  const result = []
  const seen = new Set()
  for (const raw of Array.isArray(files) ? files : []) {
    const stringValue = typeof raw === 'string' ? clean(raw) : ''
    const path = clean(raw?.path) || stringValue
    const relativePath = clean(raw?.relative_path ?? raw?.relativePath)
      || (stringValue && !stringValue.startsWith('/') ? stringValue : '')
    if (!relativePath || seen.has(relativePath)) continue
    seen.add(relativePath)
    result.push({
      path: path || relativePath,
      name: clean(raw?.name) || fileName(relativePath),
      relativePath,
    })
    if (result.length >= limit) break
  }
  return result
}

export function addContextFile(files, file, limit = 8) {
  return normalizeContextFiles([...(Array.isArray(files) ? files : []), file], limit)
}

export function contextFilePaths(files) {
  // 请求只携带工作目录内的相对引用；绝对路径仅可用于本地 tooltip。
  return normalizeContextFiles(files).map((file) => file.relativePath)
}

/**
 * 编辑器和历史记录共用的最小内容结构。相邻文本会合并；无效引用会被丢弃，
 * 但不会在这里去重，以免读取历史时悄悄改变原始顺序。
 */
export function normalizeEditorNodes(nodes) {
  const result = []
  const appendText = (text) => {
    if (!text) return
    if (result.at(-1)?.type === 'text') result.at(-1).text += text
    else result.push({ type: 'text', text })
  }
  for (const raw of Array.isArray(nodes) ? nodes : []) {
    if (raw?.type === 'text') {
      appendText(String(raw.text || ''))
      continue
    }
    if (raw?.type === 'skill') {
      const id = clean(raw.id ?? raw.skill_id)
      const label = clean(raw.label ?? raw.name) || id
      if (id && label) result.push({ type: 'skill', id, label })
      continue
    }
    if (raw?.type === 'file') {
      const relativePath = clean(raw.relativePath ?? raw.relative_path ?? raw.path)
      const label = clean(raw.label ?? raw.name) || fileName(relativePath)
      if (relativePath && label) {
        result.push({
          type: 'file', relativePath, label,
          ...(clean(raw.path) ? { path: clean(raw.path) } : {}),
        })
      }
    }
  }
  return result.length ? result : [{ type: 'text', text: '' }]
}

export function editorPlainText(nodes) {
  return normalizeEditorNodes(nodes)
    .filter((node) => node.type === 'text')
    .map((node) => node.text)
    .join('')
}

export function editorSkillIds(nodes, limit = 4) {
  const result = []
  const seen = new Set()
  for (const node of normalizeEditorNodes(nodes)) {
    if (node.type !== 'skill' || seen.has(node.id)) continue
    seen.add(node.id)
    result.push(node.id)
    if (result.length >= limit) break
  }
  return result
}

export function editorContextFiles(nodes, limit = 8) {
  return normalizeContextFiles(normalizeEditorNodes(nodes)
    .filter((node) => node.type === 'file')
    .map((node) => ({
      path: node.path || node.relativePath,
      name: node.label,
      relativePath: node.relativePath,
    })), limit)
}

function normalizeLocalMention(raw) {
  const type = raw?.type || raw?.kind
  const offset = Number(raw?.offset)
  if (!Number.isInteger(offset) || offset < 0) return null
  if (type === 'skill') {
    const id = clean(raw.skill_id ?? raw.skillId ?? raw.id)
    const label = clean(raw.name ?? raw.label) || id
    return id && label ? { type, offset, id, label } : null
  }
  if (type === 'file') {
    const relativePath = clean(raw.relative_path ?? raw.relativePath ?? raw.path)
    const label = clean(raw.name ?? raw.label) || fileName(relativePath)
    return relativePath && label ? { type, offset, relativePath, label } : null
  }
  return null
}

export function normalizeContextMentions(mentions) {
  return (Array.isArray(mentions) ? mentions : [])
    .map((mention, index) => ({ mention: normalizeLocalMention(mention), index }))
    .filter((entry) => entry.mention)
    .sort((left, right) => left.mention.offset - right.mention.offset || left.index - right.index)
    .map((entry) => entry.mention)
}

/**
 * 把编辑器快照一次性规范化为请求和本地回放所需的数据。message 中保留
 * 可见的 @标签；offset 使用 Unicode code point，避免 emoji 让前后端错位。
 */
export function serializeEditorSnapshot(nodes) {
  const normalized = normalizeEditorNodes(nodes)
  let rawMessage = ''
  const localMentions = []
  for (const node of normalized) {
    if (node.type === 'text') {
      rawMessage += node.text
      continue
    }
    const labelText = renderedMention(node)
    if (!labelText) continue
    localMentions.push({ ...node, offset: codePointLength(rawMessage) })
    rawMessage += labelText
  }

  const leading = rawMessage.match(/^\s*/u)?.[0] || ''
  const message = rawMessage.trim()
  const leadingLength = codePointLength(leading)
  const messageLength = codePointLength(message)
  const contextMentions = localMentions.map((mention) => ({
    ...mention,
    offset: Math.max(0, Math.min(messageLength, mention.offset - leadingLength)),
  }))
  const contentNodes = nodesFromMessageAndMentions(message, contextMentions)
  const skillIds = editorSkillIds(contentNodes)
  const contextFiles = editorContextFiles(contentNodes)

  return {
    message,
    plainText: editorPlainText(normalized).trim(),
    contentNodes,
    contextMentions,
    skillIds,
    skillMode: skillIds.length ? 'manual' : 'auto',
    contextFiles,
  }
}

/** 请求体不信任客户端展示名，只发送稳定标识和位置。 */
export function requestContextMentions(mentions) {
  return normalizeContextMentions(mentions).map((mention) => (
    mention.type === 'skill'
      ? { type: 'skill', offset: mention.offset, skill_id: mention.id }
      : { type: 'file', offset: mention.offset, path: mention.relativePath }
  ))
}

/**
 * 用服务端回放事件还原行内结构。新事件会带 name；旧事件没有
 * context_mentions 时自然退化成单个文本节点。
 */
export function nodesFromMessageAndMentions(message, mentions) {
  const characters = codePoints(message)
  const normalized = normalizeContextMentions(mentions)
  if (!normalized.length) return [{ type: 'text', text: String(message || '') }]

  const result = []
  let cursor = 0
  const appendText = (text) => {
    if (!text) return
    if (result.at(-1)?.type === 'text') result.at(-1).text += text
    else result.push({ type: 'text', text })
  }
  for (const mention of normalized) {
    const offset = Math.max(cursor, Math.min(characters.length, mention.offset))
    appendText(characters.slice(cursor, offset).join(''))
    const visible = codePoints(`@${mention.label}`)
    const matchesVisible = characters.slice(offset, offset + visible.length).join('') === visible.join('')
    result.push(mention.type === 'skill'
      ? { type: 'skill', id: mention.id, label: mention.label }
      : { type: 'file', relativePath: mention.relativePath, label: mention.label })
    cursor = offset + (matchesVisible ? visible.length : 0)
  }
  appendText(characters.slice(cursor).join(''))
  return normalizeEditorNodes(result)
}
