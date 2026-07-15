import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

async function loadComponentHelpers(relativePath) {
  const fileUrl = new URL(relativePath, import.meta.url)
  const source = readFileSync(fileUrl, 'utf8')
  const script = source.match(/<script>\s*([\s\S]*?)<\/script>/)?.[1]
  assert.ok(script, `${relativePath} 缺少可测试的普通 script`)
  const encoded = Buffer.from(script, 'utf8').toString('base64')
  return {
    helpers: await import(`data:text/javascript;base64,${encoded}`),
    source,
  }
}

const confirmCard = loadComponentHelpers('../src/components/ConfirmCard.vue')
const traceStep = loadComponentHelpers('../src/components/TraceStep.vue')

test('确认卡以最终门控决定为准而不被规划高风险覆盖', async () => {
  const { helpers } = await confirmCard
  const card = {
    step: { risk: 'high' },
    decision: { action: 'confirm', risk: 'medium' },
  }

  assert.equal(helpers.effectiveRiskForCard(card), 'medium')
  assert.equal(helpers.isHighRiskCard(card), false)
  assert.equal(helpers.riskLabelForCard(card), '中风险')
})

test('确认卡在缺少最终决定时才回退到规划风险', async () => {
  const { helpers } = await confirmCard
  assert.equal(helpers.isHighRiskCard({ step: { risk: 'high' } }), true)
  assert.equal(helpers.riskLabelForCard({ step: { risk: 'high' } }), '高风险')
  assert.equal(helpers.isHighRiskCard({
    step: { risk: 'low' },
    decision: { action: 'double_confirm', risk: 'medium' },
  }), true)
})

test('高风险确认卡不展示会话或目录级授权', async () => {
  const { helpers } = await confirmCard
  const choices = helpers.confirmationChoicesForCard({
    decision: { action: 'double_confirm', risk: 'high' },
    choices: ['deny', 'allow_once', 'allow_session', 'trust_path'],
  }, true)

  assert.deepEqual(choices.map((choice) => choice.id), ['deny', 'allow_once'])
})

test('软拒绝显示需授权，只有硬拒绝显示未通过', async () => {
  const { helpers } = await traceStep
  assert.deepEqual(
    helpers.rulePresentation({ decision: 'deny', hard: false }),
    { label: '需授权', tone: 'warning' },
  )
  assert.deepEqual(
    helpers.rulePresentation({ decision: 'deny', hard: true }),
    { label: '未通过', tone: 'danger' },
  )
  assert.deepEqual(
    helpers.rulePresentation({ decision: 'review' }),
    { label: '交由复核', tone: 'info' },
  )
})

test('Reviewer 展示通过状态与自身风险而非模糊的通过', async () => {
  const { helpers } = await traceStep
  assert.deepEqual(
    helpers.reviewerPresentation({ safe: true, matches_intent: true, risk: 'low' }),
    { label: '通过 · 低风险', tone: 'success' },
  )
  assert.deepEqual(
    helpers.reviewerPresentation({ safe: false, matches_intent: true, risk: 'high' }),
    { label: '告警 · 高风险', tone: 'danger' },
  )
})

test('步骤明确区分规划自评、Reviewer、最终风险与双重确认', async () => {
  const { helpers, source } = await traceStep
  assert.equal(helpers.effectiveRiskForStep({
    risk: 'low',
    verification: { decision: { risk: 'high' } },
  }), 'high')
  assert.match(source, />规划自评</)
  assert.match(source, />工具基线</)
  assert.match(source, />Reviewer</)
  assert.match(source, />最终风险</)
  assert.match(source, /double_confirm: '需要双重确认'/)
})

test('第三方 MCP 工具基线明确显示管理员策略或平台默认来源', async () => {
  const { helpers } = await traceStep
  assert.deepEqual(helpers.toolRiskPresentation({
    baseline: 'low', source: 'administrator', custom: true,
  }), {
    label: '低风险', tone: 'success',
    reason: '管理员按当前工具定义设置；定义变化后该设置会自动失效',
  })
  assert.deepEqual(helpers.toolRiskPresentation({
    baseline: 'high', source: 'platform_default', custom: true,
  }), {
    label: '高风险', tone: 'danger',
    reason: '第三方 MCP 未设置或设置已失效，平台默认按高风险处理',
  })
})

test('高风险确认的两个阶段使用不同文案', async () => {
  const { source } = await confirmCard
  assert.match(source, /高风险操作 · 第 1\/2 步/)
  assert.match(source, /继续最终确认/)
  assert.match(source, /高风险操作 · 第 2\/2 步/)
  assert.match(source, /本次仅授权当前动作/)
})
