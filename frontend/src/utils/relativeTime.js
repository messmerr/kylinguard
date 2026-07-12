export function formatRelativeTime(timestampSeconds, nowMs = Date.now()) {
  const timestamp = Number(timestampSeconds)
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '时间未知'

  const elapsedSeconds = Math.max(0, Math.floor((nowMs - timestamp * 1000) / 1000))
  if (elapsedSeconds < 60) return '刚刚'
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)} 分钟前`
  if (elapsedSeconds < 86_400) return `${Math.floor(elapsedSeconds / 3600)} 小时前`
  if (elapsedSeconds < 2_592_000) return `${Math.floor(elapsedSeconds / 86_400)} 天前`
  if (elapsedSeconds < 31_536_000) return `${Math.floor(elapsedSeconds / 2_592_000)} 个月前`
  return `${Math.floor(elapsedSeconds / 31_536_000)} 年前`
}

export function formatCollectionAge(ageSeconds) {
  const age = Math.max(0, Math.floor(Number(ageSeconds) || 0))
  if (age < 3) return '刚刚'
  if (age < 60) return `${age} 秒前`
  if (age < 3600) return `${Math.floor(age / 60)} 分钟前`
  if (age < 86_400) return `${Math.floor(age / 3600)} 小时前`
  return `${Math.floor(age / 86_400)} 天前`
}
