import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function componentSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('模型服务页在连接数量右侧添加提供商', () => {
  const models = componentSource('../src/views/ModelSettingsView.vue')
  const extensions = componentSource('../src/views/ExtensionsView.vue')

  assert.doesNotMatch(models, /管理 Agent 使用的 API 连接、模型与新任务默认值。/)
  assert.doesNotMatch(models, /\.security-note \{[^}]*margin-top:/)
  assert.match(models, /\.provider-section \{ margin-top: 0; \}/)
  assert.match(models, /\.security-note \+ \.provider-section \{ margin-top: var\(--kg-space-6\); \}/)
  assert.match(models, /\.provider-section \+ \.default-option-section \{ margin-top: var\(--kg-space-8\); \}/)
  assert.match(models, /\.default-option-section \+ \.default-option-section \{ margin-top: var\(--kg-space-3\); \}/)
  assert.match(models, /<h2 class="kg-section-title">API 提供商<\/h2>/)
  assert.equal((models.match(/aria-label="添加 API 提供商"/g) || []).length, 1)

  const sectionMeta = models.match(/<div class="section-meta">([\s\S]*?)<\/div>/)?.[1] || ''
  assert.ok(sectionMeta.indexOf('class="section-count"') >= 0)
  assert.ok(sectionMeta.indexOf('class="section-count"') < sectionMeta.indexOf('aria-label="添加 API 提供商"'))
  assert.match(sectionMeta, /size="small" type="primary"/)
  assert.match(models, /\.section-head \{ display: flex; align-items: center;/)
  assert.match(extensions, /\.section-head \{ display: flex; align-items: center;/)
  assert.match(extensions, /@media \(max-width: 700px\) \{\s*\.section-head \{ align-items: stretch;/)
  assert.doesNotMatch(models, /temperature-capability|>温度<\/el-checkbox>/)
  assert.match(models, /\.model-row \{ display: grid; grid-template-columns: minmax\(110px, 1\.1fr\) minmax\(105px, 1fr\) minmax\(120px, \.9fr\) 34px 28px;/)
})
