import assert from 'node:assert/strict'
import test from 'node:test'

import {
  cpuUsagePercent,
  diskUsagePercent,
  isMetricUnavailable,
  loadAverage,
  memoryUsagePercent,
} from '../src/utils/systemMetrics.js'

test('macOS CPU 使用率读取 idle，不把 load average 当百分比', () => {
  const raw = 'Load Avg: 1.91, 2.03, 2.19\nCPU usage: 22.98% user, 13.70% sys, 63.30% idle'
  assert.equal(Math.round(cpuUsagePercent(raw)), 37)
  assert.equal(loadAverage(raw), 1.91)
  assert.equal(cpuUsagePercent('load averages: 1.88 2.03 2.27'), null)
})

test('macOS 内存读取 memory_pressure 的空闲百分比', () => {
  assert.equal(memoryUsagePercent('System-wide memory free percentage: 68%'), 32)
})

test('磁盘容量忽略 devfs、map 和 inode 百分比', () => {
  const raw = `Filesystem Size Used Avail Capacity iused ifree %iused Mounted on
/dev/disk3s1s1 228Gi 17Gi 22Gi 44% 447k 227M 0% /
devfs 204Ki 204Ki 0Bi 100% 706 0 100% /dev
/dev/disk3s5 228Gi 171Gi 22Gi 89% 2.4M 227M 1% /System/Volumes/Data
map auto_home 0Bi 0Bi 0Bi 100% 0 0 - /System/Volumes/Data/home`
  assert.equal(diskUsagePercent(raw), 89)
})

test('平台不支持属于不可用数据', () => {
  assert.equal(isMetricUnavailable('[平台不支持] macOS 不提供 systemd'), true)
})
