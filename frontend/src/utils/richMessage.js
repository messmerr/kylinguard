const VISUAL_BLOCK = /```(echarts|mermaid)(?:\s+([^\n]+))?\s*\n([\s\S]*?)```/gi

export function parseRichMessage(sourceText = '') {
  const source = String(sourceText || '')
  const result = []
  let cursor = 0
  let index = 0
  for (const match of source.matchAll(VISUAL_BLOCK)) {
    if (match.index > cursor) {
      result.push({ type: 'markdown', content: source.slice(cursor, match.index), key: `md-${index++}` })
    }
    const type = match[1].toLowerCase()
    const title = match[2]?.trim() || (type === 'echarts' ? '交互数据视图' : 'Agent 决策流程')
    const content = match[3].trim()
    if (type === 'echarts') {
      try {
        result.push({ type, title, content, option: JSON.parse(content), key: `chart-${index++}` })
      } catch (reason) {
        result.push({ type: 'error', error: reason?.message || 'JSON 格式错误', key: `error-${index++}` })
      }
    } else {
      result.push({ type, title, content, key: `flow-${index++}` })
    }
    cursor = match.index + match[0].length
  }
  if (cursor < source.length || !result.length) {
    result.push({ type: 'markdown', content: source.slice(cursor), key: `md-${index}` })
  }
  return result
}
