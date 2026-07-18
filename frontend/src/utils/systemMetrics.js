const PSEUDO_FILESYSTEMS = new Set([
  'devfs', 'devtmpfs', 'tmpfs', 'proc', 'sysfs', 'map',
])

export function isMetricUnavailable(raw) {
  return typeof raw !== 'string'
    || !raw
    || raw.startsWith('[采集失败]')
    || raw.startsWith('[平台不支持]')
}

export function isMetricUnsupported(raw) {
  return typeof raw === 'string' && raw.startsWith('[平台不支持]')
}

export function cpuUsagePercent(raw = '') {
  const explicit = raw.match(/CPU:\s*(\d+(?:\.\d+)?)%/i)
  if (explicit) return clampPercent(Number(explicit[1]))
  const idle = raw.match(/(\d+(?:\.\d+)?)\s*%?\s*(?:id|idle)\b/i)
  return idle ? clampPercent(100 - Number(idle[1])) : null
}

export function loadAverage(raw = '') {
  const match = raw.match(/load (?:average[s]?|avg):\s*([\d.]+)/i)
  return match ? Number(match[1]) : null
}

export function memoryUsagePercent(raw = '') {
  const linux = raw.match(/Mem:\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)/i)
  if (linux && Number(linux[1]) > 0) {
    return Number(linux[2]) / Number(linux[1]) * 100
  }
  const windows = raw.match(/total=(\d+(?:\.\d+)?)MB\s+used=(\d+(?:\.\d+)?)MB/i)
  if (windows && Number(windows[1]) > 0) {
    return Number(windows[2]) / Number(windows[1]) * 100
  }
  const macos = raw.match(/System-wide memory free percentage:\s*(\d+(?:\.\d+)?)%/i)
  return macos ? clampPercent(100 - Number(macos[1])) : null
}

export function formatMemoryTable(raw = '') {
  const lines = String(raw).split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  const headerIndex = lines.findIndex(line => /^total\s+used\s+free\b/i.test(line))
  if (headerIndex < 0) return String(raw)

  const header = lines[headerIndex].split(/\s+/)
  const body = lines.slice(headerIndex + 1)
    .filter(line => /^(?:Mem|Swap):/i.test(line))
    .map(line => line.split(/\s+/))
  if (!body.length) return String(raw)

  const rows = [['', ...header], ...body]
  const columnCount = Math.max(...rows.map(row => row.length))
  const widths = Array.from({ length: columnCount }, (_, index) => (
    Math.max(...rows.map(row => (row[index] || '').length))
  ))

  return rows.map(row => (
    Array.from({ length: columnCount }, (_, index) => {
      const cell = row[index] || ''
      return index === 0 ? cell.padEnd(widths[index]) : cell.padStart(widths[index])
    }).join('  ').trimEnd()
  )).join('\n')
}

export function diskUsagePercent(raw = '') {
  let highest = null
  for (const line of String(raw).split('\n')) {
    const windows = line.match(/total=([\d.]+)G\s+used=([\d.]+)G/i)
    if (windows && Number(windows[1]) > 0) {
      highest = Math.max(highest ?? 0, Number(windows[2]) / Number(windows[1]) * 100)
      continue
    }

    const parts = line.trim().split(/\s+/)
    if (parts.length < 6 || PSEUDO_FILESYSTEMS.has(parts[0]?.toLocaleLowerCase())) continue
    const capacity = parts[4]?.match(/^(\d+(?:\.\d+)?)%$/)
    if (capacity) highest = Math.max(highest ?? 0, Number(capacity[1]))
  }
  return highest
}

function clampPercent(value) {
  if (!Number.isFinite(value)) return null
  return Math.max(0, Math.min(100, value))
}
