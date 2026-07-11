import assert from 'node:assert/strict'
import test from 'node:test'

import {
  planningProgressCount,
  planningProgressText,
} from '../src/utils/progressPresentation.js'

test('planning 活动使用自然短句且不依赖自由文本', () => {
  assert.equal(planningProgressText({
    planningActivity: 'constructing_tool_call',
    message: '{"tool":"files.write_file"}',
  }), '正在组织下一步操作…')
  assert.equal(planningProgressText({
    planningActivity: 'preparing_file_path',
    path: '/srv/private/note.md',
  }), '正在准备文件写入…')
  assert.equal(planningProgressText({
    planningActivity: 'generating_file_content',
    content: '<html>正文</html>',
  }), '正在生成文件内容…')
})

test('planning 生成计数优先展示字符数并兼容字节数', () => {
  assert.equal(planningProgressCount({ generatedChars: 6709, generatedBytes: 7041 }),
    '已生成 6,709 字符')
  assert.equal(planningProgressCount({ generatedChars: null, generatedBytes: 7041 }),
    '已生成 7,041 字节')
  assert.equal(planningProgressCount({}), '')
})
