import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PROVIDER_TEMPLATES,
  providerAdapterLabel,
  providerDiscoveryHint,
  providerTemplatePatch,
} from '../src/utils/providerTemplates.js'

test('常用模型服务模板包含官方兼容地址', () => {
  const templates = Object.fromEntries(PROVIDER_TEMPLATES.map(
    (template) => [template.id, template],
  ))

  assert.equal(templates.kimi.baseUrl, 'https://api.moonshot.cn/v1')
  assert.equal(templates.zhipu.baseUrl, 'https://open.bigmodel.cn/api/paas/v4')
  assert.equal(templates.volcengine.baseUrl, 'https://ark.cn-beijing.volces.com/api/v3')
  assert.equal(templates.minimax.baseUrl, 'https://api.minimaxi.com/v1')
  assert.equal(templates.gemini.baseUrl,
    'https://generativelanguage.googleapis.com/v1beta/openai')
  assert.equal(templates.siliconflow.baseUrl, 'https://api.siliconflow.cn/v1')
  assert.equal(templates.openrouter.baseUrl, 'https://openrouter.ai/api/v1')
  assert.equal(providerAdapterLabel('kimi'), 'Kimi / Moonshot')
  assert.match(providerDiscoveryHint('kimi'), /模型自身的思考模式/)
})

test('切换模板会预填默认值但保留用户自定义配置', () => {
  assert.deepEqual(providerTemplatePatch('kimi', { name: '', baseUrl: '' }), {
    name: 'Kimi', baseUrl: 'https://api.moonshot.cn/v1',
  })
  assert.deepEqual(providerTemplatePatch('minimax', {
    name: 'Kimi', baseUrl: 'https://api.moonshot.cn/v1/',
  }), {
    name: 'MiniMax', baseUrl: 'https://api.minimaxi.com/v1',
  })
  assert.deepEqual(providerTemplatePatch('zhipu', {
    name: '内网 GLM', baseUrl: 'https://llm.example.test/v1',
  }), {
    name: '内网 GLM', baseUrl: 'https://llm.example.test/v1',
  })
})
