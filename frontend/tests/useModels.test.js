import assert from 'node:assert/strict'
import test from 'node:test'

const storageWrites = []
globalThis.localStorage = {
  getItem: () => null,
  setItem: (key, value) => storageWrites.push([key, String(value)]),
  removeItem: () => {},
}

let configFixture = null
let sessionGetFixture = null
let sessionPutFixture = null
let sessionPutStatus = 200
let lastRequest = null
let pendingPut = null

globalThis.fetch = async (url, options = {}) => {
  lastRequest = { url, options }
  if (url === '/api/llm/config') return Response.json(configFixture)
  if (url.endsWith('/model') && options.method === 'PUT') {
    if (pendingPut) await pendingPut
    return Response.json(sessionPutFixture || {}, { status: sessionPutStatus })
  }
  if (url.endsWith('/model')) return Response.json(sessionGetFixture || {}, {
    status: sessionGetFixture ? 200 : 404,
  })
  if (url === '/api/llm/defaults') return Response.json(configFixture)
  throw new Error(`unexpected fetch: ${url}`)
}

const models = await import('../src/composables/useModels.js')

function baseConfig() {
  return {
    providers: [{
      id: 'provider-a', name: 'Provider A', adapter: 'openai_compatible',
      base_url: 'https://api.example.com/v1', enabled: true,
      api_key_configured: true, api_key: 'must-not-enter-state',
      allow_insecure_http: false,
      models: [{
        id: 'model-a', label: 'Model A', enabled: true,
        supported_efforts: ['low', 'medium', 'high'],
        supports_temperature: true,
      }, {
        id: 'model-b', label: 'Model B', enabled: true,
        supported_efforts: [], supports_temperature: false,
      }],
      last_tested_at: 1, last_test_ok: true,
    }],
    defaults: {
      version: 3,
      agent: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'medium' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-b', reasoning_effort: 'auto' },
    },
    security: { credentials_isolated: true, message: '凭据已隔离保存' },
  }
}

function reset() {
  models._resetModelStateForTests()
  configFixture = baseConfig()
  sessionGetFixture = null
  sessionPutFixture = null
  sessionPutStatus = 200
  lastRequest = null
  pendingPut = null
  storageWrites.length = 0
}

test('读取提供商与默认值时规范化能力且不保留密钥', async () => {
  reset()
  await models.loadModelConfig()

  assert.equal(models.modelProviders.value[0].name, 'Provider A')
  assert.equal(models.modelProviders.value[0].models[0].supportsTemperature, true)
  assert.deepEqual(models.modelProviders.value[0].models[0].supportedEfforts,
    ['low', 'medium', 'high'])
  assert.equal(models.modelDefaults.agent.reasoningEffort, 'medium')
  assert.equal(models.sessionModel.modelId, 'model-a')
  assert.equal(models.modelSecurity.credentialsIsolated, true)
  assert.equal(models.modelProviders.value[0].allowInsecureHttp, false)
  assert.equal(JSON.stringify(models.modelProviders.value).includes('must-not-enter-state'), false)
  assert.equal(storageWrites.length, 0)
})

test('新任务草稿只发送有效模型组合，不支持的推理档位回退自动', async () => {
  reset()
  await models.loadModelConfig()

  models.setDraftModel({
    providerId: 'provider-a', modelId: 'model-b', reasoningEffort: 'high',
  })

  assert.deepEqual(models.modelRequestPayload(), {
    provider_id: 'provider-a',
    model_id: 'model-b',
    reasoning_effort: 'auto',
  })
})

test('不存在或已停用的模型不会静默回退到其他模型', async () => {
  reset()
  await models.loadModelConfig()

  assert.throws(() => models.setDraftModel({
    providerId: 'provider-a', modelId: 'missing-model', reasoningEffort: 'auto',
  }), /当前不可用/)
  assert.equal(models.sessionModel.modelId, 'model-a')
})

test('会话模型采用服务端确认后更新并携带版本', async () => {
  reset()
  await models.loadModelConfig()
  models.bindModelSession('session-1', {
    provider_id: 'provider-a', model_id: 'model-a',
    reasoning_effort: 'low', version: 7,
  })
  sessionPutFixture = {
    model_context: {
      session_id: 'session-1', provider_id: 'provider-a', model_id: 'model-b',
      reasoning_effort: 'auto', version: 8,
    },
  }

  let release
  pendingPut = new Promise((resolve) => { release = resolve })
  const update = models.setActiveModel({
    providerId: 'provider-a', modelId: 'model-b', reasoningEffort: 'auto',
  })
  await Promise.resolve()
  assert.equal(models.sessionModel.modelId, 'model-a')
  release()
  await update

  assert.equal(lastRequest.url, '/api/sessions/session-1/model')
  assert.deepEqual(JSON.parse(lastRequest.options.body), {
    version: 7,
    provider_id: 'provider-a',
    model_id: 'model-b',
    reasoning_effort: 'auto',
  })
  assert.equal(models.sessionModel.modelId, 'model-b')
  assert.equal(models.sessionModel.version, 8)
})

test('版本冲突重新读取服务端上下文且不保留失败选择', async () => {
  reset()
  await models.loadModelConfig()
  models.bindModelSession('session-2', {
    provider_id: 'provider-a', model_id: 'model-a',
    reasoning_effort: 'medium', version: 2,
  })
  sessionPutStatus = 409
  sessionPutFixture = { detail: '模型配置已由其他请求修改' }
  sessionGetFixture = {
    model_context: {
      session_id: 'session-2', provider_id: 'provider-a', model_id: 'model-a',
      reasoning_effort: 'high', version: 3,
    },
  }

  await assert.rejects(models.setActiveModel({
    providerId: 'provider-a', modelId: 'model-b', reasoningEffort: 'auto',
  }), /其他请求修改/)

  assert.equal(models.sessionModel.modelId, 'model-a')
  assert.equal(models.sessionModel.reasoningEffort, 'high')
  assert.equal(models.sessionModel.version, 3)
})

test('保存新任务默认值使用全局版本并重新同步配置', async () => {
  reset()
  await models.loadModelConfig()
  configFixture = {
    defaults: {
      version: 4,
      agent: { provider_id: 'provider-a', model_id: 'model-b', reasoning_effort: 'auto' },
      reviewer: { provider_id: 'provider-a', model_id: 'model-a', reasoning_effort: 'low' },
    },
  }

  await models.updateModelDefaults({
    agent: { providerId: 'provider-a', modelId: 'model-b', reasoningEffort: 'auto' },
    reviewer: { providerId: 'provider-a', modelId: 'model-a', reasoningEffort: 'low' },
  })

  assert.equal(lastRequest.url, '/api/llm/defaults')
  assert.equal(models.modelDefaults.version, 4)
  assert.equal(models.modelDefaults.agent.modelId, 'model-b')
  assert.equal(models.modelProviders.value.length, 1)
  assert.equal(storageWrites.length, 0)
})
