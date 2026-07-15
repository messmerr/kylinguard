import assert from 'node:assert/strict'
import test from 'node:test'

import {
  planningProgressCount,
  planningProgressText,
  turnProgressText,
} from '../src/utils/progressPresentation.js'

test('planning 活动使用自然短句且不依赖自由文本', () => {
  assert.equal(planningProgressText({
    planningActivity: 'constructing_tool_call',
    message: '{"tool":"files.write_file"}',
  }), '正在组织下一步操作…')
  assert.equal(planningProgressText({
    planningActivity: 'preparing_file_path',
    path: '/srv/private/note.md',
  }), '正在确认文件位置…')
  assert.equal(planningProgressText({
    planningActivity: 'generating_file_content',
    content: '<html>正文</html>',
  }), '正在拟定文件内容…')
})

test('planning 生成计数优先展示字符数并兼容字节数', () => {
  assert.equal(planningProgressCount({ generatedChars: 6709, generatedBytes: 7041 }),
    '规划内容已处理 6,709 字符')
  assert.equal(planningProgressCount({ generatedChars: null, generatedBytes: 7041 }),
    '规划内容已处理 7,041 字节')
  assert.equal(planningProgressCount({}), '')
})

test('模型等待首个输出时展示为思考和复核而不是网络连接', () => {
  assert.equal(turnProgressText(
    { status: 'running', stage: 'planning' },
    { stage: 'planning', state: 'connecting' },
  ), '规划模型正在理解请求并思考下一步…')
  assert.equal(turnProgressText(
    { status: 'running', stage: 'planning' },
    { stage: 'planning', state: 'streaming' },
  ), '正在梳理思路并形成方案…')
  assert.equal(turnProgressText(
    { status: 'running', stage: 'reviewing' },
    { stage: 'reviewing', state: 'connecting' },
  ), '安全模型正在独立检查操作风险…')
})

test('回合各阶段使用明确且正向的进展反馈', () => {
  const cases = [
    [{ status: 'running', stage: 'accepting' }, null,
      '正在读取任务上下文与系统状态…'],
    [{ status: 'retry_wait', stage: 'planning' },
      { stage: 'planning', state: 'retry_wait' }, '模型请求暂时受阻，正在自动重试…'],
    [{ status: 'running', stage: 'executing' },
      { stage: 'executing', state: 'connecting' }, '正在执行操作…'],
    [{ status: 'waiting_user', stage: 'confirmation' },
      { stage: 'confirmation', state: 'waiting' }, '需要你确认后继续'],
    [{ status: 'running', stage: 'confirmation' },
      { stage: 'confirmation', state: 'completed' }, '已收到你的选择，正在继续任务…'],
    [{ status: 'running', stage: 'confirmation' },
      { stage: 'confirmation', state: 'cancelled' }, '确认已结束，正在更新任务状态…'],
    [{ status: 'running', stage: 'reviewing' },
      { stage: 'reviewing', state: 'completed' }, '已收到安全复核结果，正在确认执行条件…'],
  ]
  for (const [turn, activity, expected] of cases) {
    const text = turnProgressText(turn, activity)
    assert.equal(text, expected)
    assert.doesNotMatch(text, /正在连接|暂时没有响应/)
  }
})
