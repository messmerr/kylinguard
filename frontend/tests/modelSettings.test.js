import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createLatestSaveQueue,
  discoveredModelAdditions,
} from '../src/utils/modelSettings.js'

test('读取模型只追加新 ID 并保留手工配置', () => {
  const existing = [{
    id: 'manual-model', label: '手工名称', supportedEfforts: ['xhigh'],
  }]
  const additions = discoveredModelAdditions(existing, [
    ' manual-model ', 'new-model', 'new-model', '',
  ], 'openai_compatible')

  assert.deepEqual(existing, [{
    id: 'manual-model', label: '手工名称', supportedEfforts: ['xhigh'],
  }])
  assert.deepEqual(additions, [{
    id: 'new-model',
    label: 'new-model',
    enabled: true,
    supportedEfforts: ['low', 'medium', 'high'],
    supportsTemperature: false,
  }])
})

test('品牌适配器只为协议明确的服务声明推理档位', () => {
  const kimi = discoveredModelAdditions([], ['kimi-model'], 'kimi')
  const gemini = discoveredModelAdditions([], ['gemini-model'], 'gemini')

  assert.deepEqual(kimi[0].supportedEfforts, [])
  assert.deepEqual(gemini[0].supportedEfforts, ['low', 'medium', 'high'])
})

test('自动保存单飞且连续变更采用最新快照', async () => {
  const calls = []
  const saved = []
  const busy = []
  let releaseFirst
  const firstPending = new Promise((resolve) => { releaseFirst = resolve })
  const queue = createLatestSaveQueue(async (value) => {
    calls.push(value)
    if (value === 'first') await firstPending
  }, {
    onBusyChange: (value) => busy.push(value),
    onSaved: (value) => saved.push(value),
  })

  queue.enqueue('first')
  queue.enqueue('second')
  queue.enqueue('latest')
  await Promise.resolve()
  assert.deepEqual(calls, ['first'])

  releaseFirst()
  await queue.whenIdle()

  assert.deepEqual(calls, ['first', 'latest'])
  assert.deepEqual(saved, ['latest'])
  assert.deepEqual(busy, [true, false])
})

test('自动保存终态失败会报告错误并恢复空闲', async () => {
  const errors = []
  const busy = []
  const failure = new Error('network down')
  const queue = createLatestSaveQueue(async () => { throw failure }, {
    onBusyChange: (value) => busy.push(value),
    onError: (error, value) => errors.push([error, value]),
  })

  queue.enqueue('only')
  await queue.whenIdle()

  assert.deepEqual(errors, [[failure, 'only']])
  assert.deepEqual(busy, [true, false])
})

test('中间保存失败时仍继续提交等待中的最新快照', async () => {
  const calls = []
  const errors = []
  const saved = []
  let rejectFirst
  const firstPending = new Promise((resolve, reject) => { rejectFirst = reject })
  const queue = createLatestSaveQueue(async (value) => {
    calls.push(value)
    if (value === 'first') await firstPending
  }, {
    onError: (error) => errors.push(error),
    onSaved: (value) => saved.push(value),
  })

  queue.enqueue('first')
  queue.enqueue('latest')
  rejectFirst(new Error('stale request failed'))
  await queue.whenIdle()

  assert.deepEqual(calls, ['first', 'latest'])
  assert.deepEqual(errors, [])
  assert.deepEqual(saved, ['latest'])
})
