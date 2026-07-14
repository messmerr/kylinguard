const DISCOVERED_EFFORTS = {
  openai: ['low', 'medium', 'high'],
  openai_compatible: ['low', 'medium', 'high'],
  deepseek: ['none', 'high', 'max'],
}

function cleanModelId(value) {
  const id = String(value || '').trim()
  if (!id || id.length > 256 || [...id].some((char) => char.charCodeAt(0) < 32)) {
    return ''
  }
  return id
}

/**
 * 只生成远端新发现的模型，已有行由调用方原样保留，避免覆盖用户手工声明的能力。
 */
export function discoveredModelAdditions(existingModels, discoveredModels, adapter) {
  const known = new Set((existingModels || []).map((model) => cleanModelId(model.id)).filter(Boolean))
  const additions = []

  for (const raw of Array.isArray(discoveredModels) ? discoveredModels : []) {
    const source = typeof raw === 'string' ? { id: raw } : (raw || {})
    const id = cleanModelId(source.id ?? source.model_id)
    if (!id || known.has(id)) continue
    known.add(id)

    const rawEfforts = source.supported_efforts ?? source.supportedEfforts
    additions.push({
      id,
      label: String(source.label || source.name || id).trim() || id,
      enabled: source.enabled !== false,
      supportedEfforts: Array.isArray(rawEfforts)
        ? [...new Set(rawEfforts.map((value) => String(value).trim()).filter(Boolean))]
        : [...(DISCOVERED_EFFORTS[adapter] || [])],
      supportsTemperature: Boolean(
        source.supports_temperature ?? source.supportsTemperature,
      ),
    })
  }

  return additions
}

/**
 * 自动保存时始终单飞；请求执行期间的连续输入只保留最新快照。
 */
export function createLatestSaveQueue(save, hooks = {}) {
  let pending = null
  let running = false
  let idleWaiters = []

  async function drain() {
    if (running) return
    running = true
    hooks.onBusyChange?.(true)

    while (pending) {
      const current = pending
      pending = null
      try {
        await save(current)
        if (!pending) hooks.onSaved?.(current)
      } catch (error) {
        if (!pending) hooks.onError?.(error, current)
      }
    }

    running = false
    hooks.onBusyChange?.(false)
    const waiters = idleWaiters
    idleWaiters = []
    waiters.forEach((resolve) => resolve())
  }

  return {
    enqueue(value) {
      pending = value
      void drain()
    },
    whenIdle() {
      if (!running && !pending) return Promise.resolve()
      return new Promise((resolve) => idleWaiters.push(resolve))
    },
  }
}
