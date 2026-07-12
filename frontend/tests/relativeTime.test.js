import assert from 'node:assert/strict'
import test from 'node:test'

import { formatCollectionAge, formatRelativeTime } from '../src/utils/relativeTime.js'

const NOW = Date.UTC(2026, 6, 12, 8, 0, 0)
const ago = (seconds) => (NOW - seconds * 1000) / 1000

test('最近任务按稳定的相对时间单位展示', () => {
  assert.equal(formatRelativeTime(ago(5), NOW), '刚刚')
  assert.equal(formatRelativeTime(ago(60), NOW), '1 分钟前')
  assert.equal(formatRelativeTime(ago(59 * 60), NOW), '59 分钟前')
  assert.equal(formatRelativeTime(ago(60 * 60), NOW), '1 小时前')
  assert.equal(formatRelativeTime(ago(24 * 60 * 60), NOW), '1 天前')
  assert.equal(formatRelativeTime(ago(45 * 24 * 60 * 60), NOW), '1 个月前')
  assert.equal(formatRelativeTime(ago(400 * 24 * 60 * 60), NOW), '1 年前')
})

test('未来和无效时间不会生成负数', () => {
  assert.equal(formatRelativeTime((NOW + 60_000) / 1000, NOW), '刚刚')
  assert.equal(formatRelativeTime('', NOW), '时间未知')
})

test('采集年龄在秒、分、小时之间自然切换', () => {
  assert.equal(formatCollectionAge(2.9), '刚刚')
  assert.equal(formatCollectionAge(35.8), '35 秒前')
  assert.equal(formatCollectionAge(65), '1 分钟前')
  assert.equal(formatCollectionAge(3605), '1 小时前')
})
