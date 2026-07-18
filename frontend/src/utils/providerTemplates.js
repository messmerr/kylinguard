const DIRECT_PROVIDERS = '模型服务'
const MODEL_PLATFORMS = '模型平台'
const CUSTOM_PROVIDERS = '自定义'

/**
 * 常用服务商的 OpenAI 兼容入口。
 *
 * adapter 会持久化到后端，用来区分服务商的参数语义；baseUrl 只负责在
 * 新建连接时预填，用户仍然可以改成私有部署或区域端点。
 */
export const PROVIDER_TEMPLATES = Object.freeze([
  {
    id: 'openai', label: 'OpenAI', name: 'OpenAI', group: DIRECT_PROVIDERS,
    baseUrl: 'https://api.openai.com/v1',
    effortHint: '读取后默认开放 low / medium / high；扩展档位可批量设置。',
  },
  {
    id: 'deepseek', label: 'DeepSeek', name: 'DeepSeek', group: DIRECT_PROVIDERS,
    baseUrl: 'https://api.deepseek.com',
    effortHint: '读取后使用 DeepSeek 的 none / high / max 三档。',
  },
  {
    id: 'kimi', label: 'Kimi / Moonshot', name: 'Kimi', group: DIRECT_PROVIDERS,
    baseUrl: 'https://api.moonshot.cn/v1',
    effortHint: 'Kimi 兼容 OpenAI 接口；读取后默认沿用模型自身的思考模式。',
  },
  {
    id: 'dashscope', label: '阿里云百炼 / DashScope', name: '阿里云百炼',
    group: DIRECT_PROVIDERS,
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    effortHint: 'DashScope 能力因模型而异，可批量设置后逐模型调整。',
  },
  {
    id: 'zhipu', label: '智谱 GLM', name: '智谱 GLM', group: DIRECT_PROVIDERS,
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    effortHint: 'GLM 思考参数因模型而异；读取后默认沿用模型自身行为。',
  },
  {
    id: 'volcengine', label: '火山方舟 / 豆包', name: '火山方舟',
    group: DIRECT_PROVIDERS,
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    effortHint: '方舟思考能力因接入点和模型而异；读取后默认沿用模型自身行为。',
  },
  {
    id: 'minimax', label: 'MiniMax', name: 'MiniMax', group: DIRECT_PROVIDERS,
    baseUrl: 'https://api.minimaxi.com/v1',
    effortHint: 'MiniMax 兼容 OpenAI 接口；读取后默认沿用模型自身行为。',
  },
  {
    id: 'gemini', label: 'Google Gemini', name: 'Google Gemini',
    group: DIRECT_PROVIDERS,
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    effortHint: 'Gemini 兼容标准 reasoning_effort，读取后开放 low / medium / high。',
  },
  {
    id: 'siliconflow', label: '硅基流动 / SiliconFlow', name: '硅基流动',
    group: MODEL_PLATFORMS,
    baseUrl: 'https://api.siliconflow.cn/v1',
    effortHint: '平台内模型能力不同；读取后默认沿用模型自身行为。',
  },
  {
    id: 'openrouter', label: 'OpenRouter', name: 'OpenRouter',
    group: MODEL_PLATFORMS,
    baseUrl: 'https://openrouter.ai/api/v1',
    effortHint: '平台会按模型转发参数；读取后默认沿用所选模型自身行为。',
  },
  {
    id: 'openai_compatible', label: '其他 OpenAI Compatible', name: '',
    group: CUSTOM_PROVIDERS, baseUrl: '',
    effortHint: '兼容接口读取后默认开放 low / medium / high；仍可逐模型调整。',
  },
])

export const PROVIDER_TEMPLATE_GROUPS = Object.freeze(
  [...new Set(PROVIDER_TEMPLATES.map((template) => template.group))].map(
    (label) => Object.freeze({
      label,
      options: Object.freeze(PROVIDER_TEMPLATES.filter(
        (template) => template.group === label,
      )),
    }),
  ),
)

const TEMPLATE_BY_ID = new Map(PROVIDER_TEMPLATES.map(
  (template) => [template.id, template],
))
const TEMPLATE_NAMES = new Set(PROVIDER_TEMPLATES.map(
  (template) => template.name,
).filter(Boolean))
const TEMPLATE_URLS = new Set(PROVIDER_TEMPLATES.map(
  (template) => template.baseUrl,
).filter(Boolean))

export function providerTemplate(adapter) {
  return TEMPLATE_BY_ID.get(adapter) || TEMPLATE_BY_ID.get('openai_compatible')
}

export function providerAdapterLabel(adapter) {
  return TEMPLATE_BY_ID.get(adapter)?.label || adapter
}

export function providerDiscoveryHint(adapter) {
  return providerTemplate(adapter).effortHint
}

/**
 * 切换模板时只替换空值或上一个模板的默认值，保留用户自定义名称和地址。
 */
export function providerTemplatePatch(adapter, current = {}) {
  const template = providerTemplate(adapter)
  const name = String(current.name || '').trim()
  const baseUrl = String(current.baseUrl || '').trim().replace(/\/+$/, '')
  return {
    name: !name || TEMPLATE_NAMES.has(name) ? template.name : current.name,
    baseUrl: !baseUrl || TEMPLATE_URLS.has(baseUrl) ? template.baseUrl : current.baseUrl,
  }
}
