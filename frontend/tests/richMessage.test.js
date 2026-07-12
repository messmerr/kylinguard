import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRichMessage } from '../src/utils/richMessage.js'

test('普通 markdown 保持为单一文本段', () => {
  assert.deepEqual(parseRichMessage('## 状态\n一切正常'), [{
    type: 'markdown', content: '## 状态\n一切正常', key: 'md-0',
  }])
})

test('echarts 与 mermaid 代码块解析为消息内画布', () => {
  const segments = parseRichMessage(`资源状态
\`\`\`echarts 资源占比
{"series":[{"type":"pie","data":[{"name":"内存","value":42}]}]}
\`\`\`
执行链
\`\`\`mermaid 安全决策
flowchart LR
  A[感知] --> B[规划] --> C[执行]
\`\`\``)

  assert.deepEqual(segments.map(item => item.type), [
    'markdown', 'echarts', 'markdown', 'mermaid',
  ])
  assert.equal(segments[1].title, '资源占比')
  assert.equal(segments[1].option.series[0].type, 'pie')
  assert.equal(segments[3].title, '安全决策')
  assert.match(segments[3].content, /flowchart LR/)
})

test('无效 echarts JSON 转为安全错误段', () => {
  const [segment] = parseRichMessage('```echarts\n{bad json}\n```')
  assert.equal(segment.type, 'error')
  assert.ok(segment.error)
})
