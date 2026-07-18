<template>
  <div class="kg-page extensions-page">
    <div class="kg-page-inner extensions-inner">
      <div v-if="extensionError" class="extension-error" role="alert">
        <KgIcon name="warning" :size="15" />
        <span>{{ extensionError }}</span>
        <el-button text :loading="extensionsLoading" @click="reloadExtensions">重试</el-button>
      </div>
      <div
        v-else-if="extensionsLoading && !mcpServers.length && !extensionSkills.length"
        class="extension-loading"
        role="status"
      >
        <span class="kg-spinner" aria-hidden="true"></span>
        <span><strong>正在同步扩展配置…</strong><small>完成后会分别显示 MCP 与 Skills</small></span>
      </div>
      <div v-if="extensionSkillIssues.length" class="skill-issues" role="alert">
        <KgIcon name="warning" :size="15" />
        <div>
          <strong>{{ extensionSkillIssues.length }} 个 Skill 未能加载</strong>
          <span v-for="issue in extensionSkillIssues" :key="`${issue.source}:${issue.id}`">
            {{ issue.id || '未知项' }}：{{ issue.message }}
          </span>
        </div>
      </div>

      <section class="extension-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">第三方 MCP</h2>
            <p>添加你信任的本机 stdio MCP。这里不包含系统自带 MCP。</p>
          </div>
          <div class="section-meta">
            <span class="section-count">{{ extensionsLoading && mcpServers.length
              ? `正在同步 · ${mcpServers.length} 个` : extensionError && !mcpServers.length
                ? '状态未同步' : `${mcpServers.length} 个第三方服务` }}</span>
            <el-button size="small" type="primary" @click="openMcpImportDialog">
              <KgIcon name="plus" :size="14" />添加 MCP
            </el-button>
          </div>
        </div>

        <el-table v-if="mcpServers.length" :data="mcpServers" class="extension-table mcp-table">
          <el-table-column label="服务" min-width="170">
            <template #default="{ row }">
              <div class="name-cell">
                <span class="state-dot" :class="{ enabled: row.enabled }"></span>
                <span><strong>{{ row.name }}</strong><small>{{ row.id }}</small></span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="启动命令" min-width="260">
            <template #default="{ row }">
              <code class="command" :title="commandText(row)">{{ commandText(row) }}</code>
            </template>
          </el-table-column>
          <el-table-column label="工具" width="78" align="center">
            <template #default="{ row }">
              <span :title="row.tools.map(tool => tool.name).join('、')">{{ row.toolCount }}</span>
            </template>
          </el-table-column>
          <el-table-column label="连接状态" width="112">
            <template #default="{ row }">
              <span class="connection-state" :class="mcpStatus(row).tone" :title="row.error">
                {{ mcpStatus(row).label }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="72" align="center">
            <template #default="{ row }">
              <el-switch
                :model-value="row.enabled"
                size="small"
                :loading="extensionActionBusy('mcp:enabled', row.id)"
                :disabled="rowActionBusy(row.id)"
                :aria-label="`${row.name} MCP 服务启用状态`"
                @change="toggleMcp(row, $event)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="218" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button text :disabled="!mcpDetailAvailable(row)" @click="openMcpDetail(row)">详情</el-button>
                <el-button
                  text
                  :loading="extensionActionBusy('mcp:test', row.id)"
                  :disabled="rowActionBusy(row.id)"
                  @click="testMcp(row)"
                >测试</el-button>
                <el-button text :disabled="rowActionBusy(row.id)" @click="openMcpDialog(row)">编辑</el-button>
                <el-button text type="danger" :disabled="rowActionBusy(row.id)" @click="removeMcp(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div v-else-if="!extensionsLoading && !extensionError" class="empty-state">
          <span class="empty-mark"><KgIcon name="server" :size="20" /></span>
          <div><strong>还没有第三方 MCP</strong><p>点击添加后，可以粘贴现有配置，也可以手动填写。</p></div>
          <el-button type="primary" @click="openMcpImportDialog"><KgIcon name="plus" :size="14" />添加 MCP</el-button>
        </div>
      </section>

      <section class="extension-section skill-section">
        <div class="section-head">
          <div>
            <h2 class="kg-section-title">Skills</h2>
            <p>Skill 提供任务方法与工作流程；启用后模型可以自动匹配，也可以通过 @ 明确指定。</p>
          </div>
          <div class="section-meta">
            <span class="section-count">{{ extensionsLoading && extensionSkills.length
              ? `正在同步 · ${extensionSkills.length} 个` : extensionError && !extensionSkills.length
                ? '状态未同步' : `${extensionSkills.length} 个 Skill` }}</span>
            <el-button size="small" type="primary" @click="openSkillImportDialog">
              <KgIcon name="plus" :size="14" />添加 Skill
            </el-button>
          </div>
        </div>

        <el-table v-if="extensionSkills.length" :data="extensionSkills" class="extension-table skill-table">
          <el-table-column label="Skill" min-width="190">
            <template #default="{ row }">
              <div class="name-cell skill-name">
                <span class="skill-mark"><KgIcon name="task" :size="14" /></span>
                <span><strong>{{ row.name }}</strong><small>{{ row.id }}</small></span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="260">
            <template #default="{ row }"><span class="description" :title="row.description">{{ row.description || '暂无说明' }}</span></template>
          </el-table-column>
          <el-table-column label="来源" width="96">
            <template #default="{ row }"><span class="source-badge">{{ sourceLabel(row.source) }}</span></template>
          </el-table-column>
          <el-table-column label="工具依赖" min-width="145">
            <template #default="{ row }">
              <span v-if="row.requiredTools.length" class="tool-summary" :title="row.requiredTools.join('、')">
                {{ row.requiredTools.slice(0, 2).join('、') }}<template v-if="row.requiredTools.length > 2"> 等 {{ row.requiredTools.length }} 个</template>
              </span>
              <span v-else class="muted">无额外依赖</span>
              <small v-if="row.missingTools.length" class="missing-tools">缺少 {{ row.missingTools.length }} 个</small>
            </template>
          </el-table-column>
          <el-table-column label="使用方式" width="112">
            <template #default><span class="mode-badge">自动匹配 + @</span></template>
          </el-table-column>
          <el-table-column label="启用" width="72" align="center">
            <template #default="{ row }">
              <el-switch
                :model-value="row.enabled"
                size="small"
                :loading="extensionActionBusy('skill:enabled', row.id)"
                :disabled="skillActionBusy(row.id)"
                :aria-label="`${row.name} Skill 启用状态`"
                @change="toggleSkill(row, $event)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="178" align="center">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button text @click="openSkillDetail(row)">详情</el-button>
                <el-button
                  text
                  :class="{ 'restricted-skill-action': row.source === 'builtin' }"
                  :disabled="row.source !== 'builtin' && skillActionBusy(row.id)"
                  @click="openSkillDialog(row)"
                >编辑</el-button>
                <el-button
                  text
                  type="danger"
                  :class="{ 'restricted-skill-action': row.source === 'builtin' }"
                  :disabled="row.source !== 'builtin' && skillActionBusy(row.id)"
                  @click="removeSkill(row)"
                >删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div v-else-if="!extensionsLoading && !extensionError" class="empty-state">
          <span class="empty-mark"><KgIcon name="task" :size="20" /></span>
          <div><strong>还没有 Skill</strong><p>点击添加后，可以选择 SKILL.md，也可以手动创建。</p></div>
          <el-button type="primary" @click="openSkillImportDialog"><KgIcon name="plus" :size="14" />添加 Skill</el-button>
        </div>
      </section>
    </div>

    <el-dialog
      v-model="mcpImportOpen"
      title="添加第三方 MCP"
      width="min(620px, calc(100vw - 28px))"
      align-center
    >
      <p class="import-intro"><span class="recommended-chip">推荐</span>粘贴常见的 <code>mcpServers</code> JSON、单个 MCP JSON，或一条完整的 stdio 启动命令。</p>
      <el-input
        v-model="mcpImportText"
        type="textarea"
        :rows="12"
        aria-label="MCP 配置或启动命令"
        placeholder='{&#10;  "mcpServers": {&#10;    "my-server": { "command": "/usr/local/bin/server", "args": ["--stdio"] }&#10;  }&#10;}'
        @input="clearMcpImportChoices"
      />
      <div v-if="mcpImportChoices.length" class="import-choice">
        <span>配置中包含 {{ mcpImportChoices.length }} 个服务，请选择本次要填入的一个：</span>
        <el-select v-model="mcpImportSelectedId" placeholder="选择 MCP 服务" aria-label="选择要导入的 MCP 服务">
          <el-option v-for="choice in mcpImportChoices" :key="choice.id" :label="choice.name === choice.id ? choice.id : `${choice.name}（${choice.id}）`" :value="choice.id" />
        </el-select>
      </div>
      <div class="safety-note import-note"><KgIcon name="info" :size="15" /><span>导入后会生成配置草稿；确认后再保存和启用。一次添加一个服务。</span></div>
      <template #footer>
        <div class="import-footer">
          <el-button @click="startManualMcp">手动填写</el-button>
          <span></span>
          <el-button @click="mcpImportOpen = false">取消</el-button>
          <el-button type="primary" @click="applyMcpImport">解析并继续</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="skillImportOpen"
      title="添加 Skill"
      width="min(620px, calc(100vw - 28px))"
      align-center
    >
      <p class="import-intro"><span class="recommended-chip">推荐</span>粘贴一个完整的 <code>SKILL.md</code>，或从本机选择文件。</p>
      <el-input
        v-model="skillImportText"
        type="textarea"
        :rows="12"
        aria-label="SKILL.md 内容"
        placeholder="---&#10;name: log-review&#10;description: 检查近期错误日志&#10;---&#10;&#10;先读取近期日志，再总结异常。"
      />
      <input ref="skillFileInput" class="hidden-file-input" type="file" accept=".md,text/markdown,text/plain" @change="importSkillFile">
      <div class="safety-note import-note"><KgIcon name="info" :size="15" /><span>支持单个 SKILL.md；添加后默认停用。</span></div>
      <template #footer>
        <div class="import-footer">
          <div class="import-secondary-actions">
            <el-button @click="skillFileInput?.click()">选择本机 SKILL.md</el-button>
            <el-button text @click="startManualSkill">手动创建</el-button>
          </div>
          <span></span>
          <el-button @click="skillImportOpen = false">取消</el-button>
          <el-button type="primary" @click="applySkillImport">解析并继续</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="mcpDialogOpen"
      class="extension-edit-dialog"
      :title="mcpForm.originalId ? '编辑第三方 MCP' : '添加第三方 MCP'"
      width="min(660px, calc(100vw - 28px))"
      align-center
      destroy-on-close
      :show-close="!mcpSaving"
      :close-on-click-modal="!mcpSaving"
      :close-on-press-escape="!mcpSaving"
      @closed="clearMcpForm"
    >
      <el-form label-position="top" :disabled="mcpSaving" @submit.prevent>
        <div v-if="mcpImportWarnings.length" class="scope-warning import-warning">
          <KgIcon name="warning" :size="15" />
          <div><strong>配置中仍有待处理项</strong><span v-for="warning in mcpImportWarnings" :key="warning">{{ warning }}</span></div>
        </div>
        <div class="form-grid">
          <el-form-item label="名称"><el-input v-model="mcpForm.name" maxlength="80" placeholder="例如 文件分析工具" /></el-form-item>
          <el-form-item label="标识">
            <el-input v-model="mcpForm.id" maxlength="64" placeholder="file-tools" :disabled="Boolean(mcpForm.originalId)" />
          </el-form-item>
        </div>
        <el-form-item label="启动命令（绝对路径）">
          <el-input v-model="mcpForm.command" placeholder="/usr/bin/python3" />
          <span class="field-note">命令在 Agent 所在主机上运行。</span>
        </el-form-item>
        <el-form-item label="工作目录（可选，绝对路径）">
          <el-input v-model="mcpForm.cwd" placeholder="留空时使用启动命令所在目录" />
          <span class="field-note">用于解析服务使用的相对路径，不改变现有权限。</span>
        </el-form-item>
        <el-form-item label="参数（每行一个）">
          <el-input v-model="mcpForm.argsText" type="textarea" :rows="3" placeholder="/opt/tools/server.py&#10;--stdio" />
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="普通环境变量">
            <el-input v-model="mcpForm.envText" type="textarea" :rows="4" placeholder="LOG_LEVEL=info" />
            <span class="field-note">每行 KEY=value；会随配置返回。</span>
          </el-form-item>
          <el-form-item label="敏感环境变量（仅写入）">
            <el-input v-model="mcpForm.secretEnvText" type="textarea" :rows="4" placeholder="API_TOKEN=..." />
            <span class="field-note">保存后不返回值<template v-if="mcpForm.secretEnvKeys.length">；已配置 {{ mcpForm.secretEnvKeys.join('、') }}</template>。</span>
            <el-select
              v-if="mcpForm.secretEnvKeys.length"
              v-model="mcpForm.clearSecretEnvKeys"
              multiple
              collapse-tags
              class="secret-clear-select"
              placeholder="选择要移除的已保存变量"
              aria-label="选择要移除的已保存敏感环境变量"
            >
              <el-option v-for="key in mcpForm.secretEnvKeys" :key="key" :label="key" :value="key" />
            </el-select>
          </el-form-item>
        </div>
        <div class="safety-note"><KgIcon name="info" :size="15" /><span>保存后不会自动启动；启用前请确认程序来源。</span></div>
      </el-form>
      <template #footer>
        <el-button :disabled="mcpSaving" @click="mcpDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="mcpSaving" @click="saveMcp">{{ mcpForm.originalId ? '保存更改' : '添加服务' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mcpDetailOpen" title="第三方 MCP 工具详情" width="min(680px, calc(100vw - 28px))" align-center>
      <template v-if="selectedMcp">
        <div class="detail-head">
          <span class="skill-mark large"><KgIcon name="server" :size="17" /></span>
          <div><strong>{{ selectedMcp.name }}</strong><span>{{ selectedMcp.id }} · {{ selectedMcp.toolCount }} 个工具</span></div>
        </div>
        <div class="safety-note detail-safety"><KgIcon name="warning" :size="15" /><span>未由管理员分级的第三方工具仍按高风险处理。MCP 服务自述仅供参考，不会自动获得权限。</span></div>
        <div v-if="selectedMcp.enabled" class="scope-warning mcp-policy-warning">
          <KgIcon name="warning" :size="15" />
          <span>风险分级会改变自动执行与确认范围；请先停用该 MCP，再修改分级。</span>
        </div>
        <div class="mcp-tool-list">
          <article v-for="tool in selectedMcp.tools" :key="tool.name" class="mcp-tool-detail">
            <div class="mcp-tool-title">
              <code>{{ selectedMcp.id }}.{{ tool.name }}</code>
              <el-select
                v-model="mcpToolRiskDraft[tool.name]"
                size="small"
                :disabled="selectedMcp.enabled || mcpPolicySaving"
                :aria-label="`${tool.name} 的管理员风险分级`"
              >
                <el-option label="平台默认（高风险）" value="default" />
                <el-option label="低风险（只读）" value="low" />
                <el-option label="中风险（可逆变更）" value="medium" />
                <el-option label="高风险（二次确认）" value="high" />
              </el-select>
            </div>
            <div class="mcp-risk-summary">
              <span :class="`risk-${tool.effectiveRisk}`">{{ effectiveRiskLabel(tool) }}</span>
              <small v-if="tool.policyStatus === 'stale'">工具定义已变化，旧分级已失效</small>
              <small v-if="annotationSummary(tool)">服务自述：{{ annotationSummary(tool) }}（不自动采纳）</small>
            </div>
            <p>{{ tool.description || '无说明' }}</p>
            <pre>{{ JSON.stringify(tool.inputSchema, null, 2) }}</pre>
          </article>
        </div>
      </template>
      <template #footer>
        <el-button :disabled="mcpPolicySaving" @click="mcpDetailOpen = false">关闭</el-button>
        <el-button
          v-if="mcpDetailAvailable(selectedMcp)"
          type="primary"
          :loading="mcpPolicySaving"
          :disabled="selectedMcp?.enabled"
          @click="saveMcpToolPolicies"
        >{{ selectedMcp?.tools.length ? '保存风险分级' : '清空风险分级' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="skillDialogOpen"
      class="extension-edit-dialog"
      :title="skillForm.originalId ? '编辑 Skill' : '添加 Skill'"
      width="min(680px, calc(100vw - 28px))"
      align-center
      destroy-on-close
      :show-close="!skillSaving"
      :close-on-click-modal="!skillSaving"
      :close-on-press-escape="!skillSaving"
      @closed="clearSkillForm"
    >
      <el-form label-position="top" :disabled="skillSaving" @submit.prevent>
        <div v-if="skillImportWarnings.length" class="scope-warning import-warning">
          <KgIcon name="warning" :size="15" />
          <div><strong>已忽略不适用的元数据</strong><span v-for="warning in skillImportWarnings" :key="warning">{{ warning }}</span></div>
        </div>
        <div class="form-grid">
          <el-form-item label="名称"><el-input v-model="skillForm.name" maxlength="100" placeholder="例如 日志故障排查" /></el-form-item>
          <el-form-item label="标识"><el-input v-model="skillForm.id" maxlength="64" placeholder="log-troubleshooting" :disabled="Boolean(skillForm.originalId)" /></el-form-item>
        </div>
        <el-form-item label="版本"><el-input v-model="skillForm.version" maxlength="32" placeholder="1.0.0" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="skillForm.description" type="textarea" :rows="2" maxlength="1024" /></el-form-item>
        <el-form-item label="工具依赖（可选）">
          <el-input v-model="skillForm.requiredToolsText" type="textarea" :rows="3" placeholder="files.read&#10;logs.recent" />
          <span class="field-note">用于检查对应 MCP 工具是否可用。</span>
        </el-form-item>
        <el-form-item label="Skill 指令">
          <el-input v-model="skillForm.instructions" type="textarea" :rows="8" placeholder="描述适用场景、处理步骤和输出要求。" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="skillSaving" @click="skillDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="skillSaving" @click="saveSkill">{{ skillForm.originalId ? '保存更改' : '添加 Skill' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="skillDetailOpen" title="Skill 详情" width="min(620px, calc(100vw - 28px))" align-center>
      <template v-if="selectedSkill">
        <div class="detail-head"><span class="skill-mark large"><KgIcon name="task" :size="17" /></span><div><strong>{{ selectedSkill.name }}</strong><span>{{ selectedSkill.id }}<template v-if="selectedSkill.version"> · v{{ selectedSkill.version }}</template></span></div></div>
        <p class="detail-description">{{ selectedSkill.description || '暂无说明' }}</p>
        <dl class="detail-list">
          <div><dt>来源</dt><dd>{{ sourceLabel(selectedSkill.source) }}</dd></div>
          <div><dt>使用方式</dt><dd>模型自动匹配，或通过 @ 明确指定</dd></div>
          <div><dt>工具依赖</dt><dd>{{ selectedSkill.requiredTools.join('、') || '无额外依赖' }}</dd></div>
        </dl>
        <div v-if="selectedSkill.instructions" class="instructions"><strong>指令</strong><pre>{{ selectedSkill.instructions }}</pre></div>
      </template>
      <template #footer><el-button @click="skillDetailOpen = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import KgIcon from '../components/KgIcon.vue'
import {
  createMcpServer,
  createSkill,
  deleteMcpServer,
  deleteSkill,
  extensionActionBusy,
  extensionError,
  extensionSkillIssues,
  extensionSkills,
  extensionsLoading,
  loadExtensions,
  mcpServers,
  setMcpServerEnabled,
  setMcpToolPolicies,
  setSkillEnabled,
  testMcpServer,
  updateMcpServer,
  updateSkill,
} from '../composables/useExtensions.js'
import {
  formatArgs,
  formatEnv,
  formatList,
  mcpFormPayload,
  mcpStatus,
  parseMcpImport,
  parseSkillImport,
  skillFormPayload,
} from '../utils/extensions.js'

const mcpImportOpen = ref(false)
const mcpImportText = ref('')
const mcpImportChoices = ref([])
const mcpImportSelectedId = ref('')
const mcpImportWarnings = ref([])
const mcpDialogOpen = ref(false)
const mcpDetailOpen = ref(false)
const selectedMcp = ref(null)
const mcpSaving = ref(false)
const mcpPolicySaving = ref(false)
const mcpToolRiskDraft = reactive({})
const skillDialogOpen = ref(false)
const skillSaving = ref(false)
const skillDetailOpen = ref(false)
const selectedSkill = ref(null)
const skillImportOpen = ref(false)
const skillImportText = ref('')
const skillFileInput = ref(null)
const skillImportWarnings = ref([])

const mcpForm = reactive({
  id: '', originalId: '', version: 0, name: '', command: '', cwd: '', argsText: '', envText: '',
  secretEnvText: '', secretEnvKeys: [], clearSecretEnvKeys: [],
})
const skillForm = reactive({
  id: '', originalId: '', name: '', description: '', version: '1.0.0',
  requiredToolsText: '',
  instructions: '', enabled: false, expectedSha256: '',
})

onMounted(() => loadExtensions().catch((error) => {
  ElMessage.error(error.message || '扩展配置读取失败')
}))

function reloadExtensions() {
  loadExtensions().catch((error) => ElMessage.error(error.message || '扩展配置读取失败'))
}

function commandText(server) {
  return [server.command, ...server.args].filter(Boolean).join(' ')
}

function sourceLabel(source) {
  if (source === 'builtin') return '内置'
  if (source === 'user') return '自定义'
  return source || '自定义'
}

function rowActionBusy(id) {
  return ['mcp:enabled', 'mcp:test', 'mcp:update', 'mcp:delete', 'mcp:policies']
    .some((kind) => extensionActionBusy(kind, id))
}

function mcpDetailAvailable(server) {
  return Boolean(server?.tools?.length || Object.keys(server?.toolPolicies || {}).length)
}

function skillActionBusy(id) {
  return ['skill:enabled', 'skill:update', 'skill:delete']
    .some((kind) => extensionActionBusy(kind, id))
}

function clearMcpForm() {
  mcpImportWarnings.value = []
  Object.assign(mcpForm, {
    id: '', originalId: '', version: 0, name: '', command: '', cwd: '', argsText: '', envText: '',
    secretEnvText: '', secretEnvKeys: [], clearSecretEnvKeys: [],
  })
}

function openMcpImportDialog() {
  mcpImportText.value = ''
  clearMcpImportChoices()
  mcpImportOpen.value = true
}

function startManualMcp() {
  mcpImportText.value = ''
  clearMcpImportChoices()
  mcpImportOpen.value = false
  openMcpDialog()
}

function clearMcpImportChoices() {
  mcpImportChoices.value = []
  mcpImportSelectedId.value = ''
}

function applyMcpImport() {
  let imported
  try {
    imported = parseMcpImport(mcpImportText.value, mcpImportSelectedId.value)
  } catch (error) {
    ElMessage.warning(error.message || 'MCP 配置解析失败')
    return
  }
  if (imported.choices) {
    mcpImportChoices.value = imported.choices
    mcpImportSelectedId.value = ''
    ElMessage.info('该配置包含多个服务，请先选择本次要添加的一个')
    return
  }
  clearMcpForm()
  Object.assign(mcpForm, {
    id: imported.id,
    name: imported.name,
    command: imported.command,
    cwd: imported.cwd,
    argsText: formatArgs(imported.args),
    envText: formatEnv(imported.env),
    secretEnvText: formatEnv(imported.secretEnv),
  })
  mcpImportWarnings.value = imported.warnings
  mcpImportText.value = ''
  clearMcpImportChoices()
  mcpImportOpen.value = false
  mcpDialogOpen.value = true
  ElMessage.success('配置已填入表单，请复核后再保存')
}

function openMcpDialog(server = null) {
  clearMcpForm()
  if (server?.enabled) {
    ElMessage.warning('请先停用 MCP 服务，再编辑启动配置')
    return
  }
  if (server) Object.assign(mcpForm, {
    id: server.id,
    originalId: server.id,
    version: server.version,
    name: server.name,
    command: server.command,
    cwd: server.cwd,
    argsText: formatArgs(server.args),
    envText: formatEnv(server.env),
    secretEnvKeys: [...server.secretEnvKeys],
    clearSecretEnvKeys: [],
  })
  mcpDialogOpen.value = true
}

function openMcpDetail(server) {
  for (const key of Object.keys(mcpToolRiskDraft)) delete mcpToolRiskDraft[key]
  for (const tool of server.tools) {
    mcpToolRiskDraft[tool.name] = (
      tool.policyStatus === 'active' && tool.riskSource === 'administrator'
        ? tool.effectiveRisk : 'default'
    )
  }
  selectedMcp.value = server
  mcpDetailOpen.value = true
}

function effectiveRiskLabel(tool) {
  const level = { low: '低风险', medium: '中风险', high: '高风险' }[tool.effectiveRisk] || '高风险'
  const source = tool.riskSource === 'administrator' ? '管理员设置' : '平台默认'
  return `${level} · ${source}`
}

function annotationSummary(tool) {
  const value = tool.annotations || {}
  const labels = []
  if (value.readOnlyHint === true) labels.push('声称只读')
  if (value.destructiveHint === true) labels.push('可能破坏数据')
  if (value.idempotentHint === true) labels.push('声称幂等')
  if (value.openWorldHint === true) labels.push('可能访问外部系统')
  return labels.join('、')
}

async function saveMcpToolPolicies() {
  const server = selectedMcp.value
  if (!server || server.enabled) return
  const policies = {}
  for (const tool of server.tools) {
    const risk = mcpToolRiskDraft[tool.name]
    if (risk && risk !== 'default') {
      policies[tool.name] = {
        risk,
        definition_sha256: tool.definitionSha256,
      }
    }
  }
  mcpPolicySaving.value = true
  try {
    await setMcpToolPolicies(server.id, server.version, policies)
    mcpDetailOpen.value = false
    ElMessage.success('MCP 工具风险分级已保存')
  } catch (error) {
    ElMessage.error(error.message || 'MCP 工具风险分级保存失败')
  } finally {
    mcpPolicySaving.value = false
  }
}

async function saveMcp() {
  let payload
  try {
    payload = mcpFormPayload(mcpForm, { editing: Boolean(mcpForm.originalId) })
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  mcpSaving.value = true
  try {
    if (mcpForm.originalId) await updateMcpServer(mcpForm.originalId, payload)
    else await createMcpServer(payload)
    mcpDialogOpen.value = false
    ElMessage.success(mcpForm.originalId ? 'MCP 服务已保存' : 'MCP 服务已添加，当前为停用状态')
  } catch (error) {
    ElMessage.error(error.message || 'MCP 服务保存失败')
  } finally {
    mcpSaving.value = false
  }
}

async function toggleMcp(server, enabled) {
  if (enabled) {
    try {
      await ElMessageBox.confirm(
        `启用后，后端主机会启动“${server.command}”。请确认这是可信的绝对路径程序。`,
        '确认启用 MCP 服务',
        { type: 'warning', confirmButtonText: '确认启用', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  try {
    await setMcpServerEnabled(server.id, enabled, server.version)
    ElMessage.success(enabled ? 'MCP 服务已启用' : 'MCP 服务已停用')
  } catch (error) {
    ElMessage.error(error.message || 'MCP 服务状态修改失败')
  }
}

async function testMcp(server) {
  try {
    await ElMessageBox.confirm(
      `测试会在后端主机临时启动“${server.command}”。请确认该程序已经审查且可信。`,
      '确认测试 MCP 服务',
      { type: 'warning', confirmButtonText: '启动测试', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await testMcpServer(server.id, server.version)
    ElMessage.success('MCP 连接测试完成')
  } catch (error) {
    ElMessage.error(error.message || 'MCP 连接测试失败')
  }
}

async function removeMcp(server) {
  try {
    await ElMessageBox.confirm(`确定删除“${server.name}”？`, '确认删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteMcpServer(server.id, server.version)
    ElMessage.success('MCP 服务已删除')
  } catch (error) {
    ElMessage.error(error.message || 'MCP 服务删除失败')
  }
}

function clearSkillForm() {
  skillImportWarnings.value = []
  Object.assign(skillForm, {
    id: '', originalId: '', name: '', description: '', version: '1.0.0',
    requiredToolsText: '',
    instructions: '', enabled: false, expectedSha256: '',
  })
}

function openSkillImportDialog() {
  skillImportText.value = ''
  skillImportOpen.value = true
}

function startManualSkill() {
  skillImportText.value = ''
  skillImportOpen.value = false
  openSkillDialog()
}

function fillSkillImport(imported) {
  clearSkillForm()
  Object.assign(skillForm, {
    id: imported.id,
    name: imported.name,
    description: imported.description,
    version: imported.version,
    requiredToolsText: formatList(imported.requiredTools),
    instructions: imported.instructions,
    enabled: false,
  })
  skillImportWarnings.value = imported.warnings || []
  skillImportText.value = ''
  skillImportOpen.value = false
  skillDialogOpen.value = true
  ElMessage.success('SKILL.md 已填入表单，请复核后再添加')
}

function applySkillImport() {
  try {
    fillSkillImport(parseSkillImport(skillImportText.value))
  } catch (error) {
    ElMessage.warning(error.message || 'SKILL.md 解析失败')
  }
}

async function importSkillFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.name.toLowerCase() !== 'skill.md') {
    ElMessage.warning('请选择名为 SKILL.md 的单个文件')
    return
  }
  if (file.size > 128 * 1024) {
    ElMessage.warning('SKILL.md 不能超过 128 KiB')
    return
  }
  try {
    skillImportText.value = await file.text()
    applySkillImport()
  } catch {
    ElMessage.error('无法读取该 SKILL.md，请确认它是 UTF-8 文本文件')
  }
}

function openSkillDialog(skill = null) {
  clearSkillForm()
  if (skill?.source === 'builtin') {
    showBuiltinSkillRestriction('编辑')
    return
  }
  if (skill) Object.assign(skillForm, {
    id: skill.id,
    originalId: skill.id,
    name: skill.name,
    description: skill.description,
    version: skill.version,
    enabled: skill.enabled,
    expectedSha256: skill.sha256,
    requiredToolsText: formatList(skill.requiredTools),
    instructions: skill.instructions,
  })
  skillDialogOpen.value = true
}

async function saveSkill() {
  let payload
  try {
    payload = skillFormPayload(skillForm, { editing: Boolean(skillForm.originalId) })
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  skillSaving.value = true
  try {
    if (skillForm.originalId) await updateSkill(skillForm.originalId, payload)
    else await createSkill(payload)
    skillDialogOpen.value = false
    ElMessage.success(skillForm.originalId ? 'Skill 已保存' : 'Skill 已添加，当前为停用状态')
  } catch (error) {
    ElMessage.error(error.message || 'Skill 保存失败')
  } finally {
    skillSaving.value = false
  }
}

async function toggleSkill(skill, enabled) {
  try {
    await setSkillEnabled(skill.id, enabled, skill.sha256, skill.enabled)
    ElMessage.success(enabled ? 'Skill 已启用' : 'Skill 已停用')
  } catch (error) {
    ElMessage.error(error.message || 'Skill 状态修改失败')
  }
}

function openSkillDetail(skill) {
  selectedSkill.value = skill
  skillDetailOpen.value = true
}

async function removeSkill(skill) {
  if (skill?.source === 'builtin') {
    await showBuiltinSkillRestriction('删除')
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除“${skill.name}”？`, '确认删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteSkill(skill.id, skill.sha256, skill.enabled)
    ElMessage.success('Skill 已删除')
  } catch (error) {
    ElMessage.error(error.message || 'Skill 删除失败')
  }
}

function showBuiltinSkillRestriction(action) {
  const message = action === '删除'
    ? '该 Skill 为平台内置，不可删除。'
    : '该 Skill 为平台内置，不可编辑。'
  return ElMessageBox.alert(message, '操作受限', {
    type: 'info', confirmButtonText: '我知道了',
  }).catch(() => {})
}
</script>

<style scoped>
.extensions-inner { width: min(100%, 1160px); }
.extension-error { min-height: 40px; display: flex; align-items: center; gap: 8px; margin-top: var(--kg-space-4); padding: 7px 10px; border: 1px solid var(--kg-danger-border); border-radius: var(--kg-radius-sm); background: var(--kg-danger-soft); color: var(--kg-danger); font-size: 12px; }
.extension-error > span { min-width: 0; flex: 1; }
.extension-loading { min-height: 48px; display: flex; align-items: center; gap: 10px; margin-top: var(--kg-space-4); padding: 8px 11px; border: 1px solid var(--kg-info-border); border-radius: var(--kg-radius-sm); background: var(--kg-info-soft); color: var(--kg-info); }
.extension-loading > span:last-child { display: grid; gap: 2px; }
.extension-loading strong { color: var(--kg-text-secondary); font-size: 12px; font-weight: 550; }
.extension-loading small { color: var(--kg-text-tertiary); font-size: 11px; }
.skill-issues { display: flex; align-items: flex-start; gap: 8px; margin-top: var(--kg-space-3); padding: 9px 11px; border: 1px solid var(--kg-warning-border); border-radius: var(--kg-radius-sm); background: var(--kg-warning-soft); color: var(--kg-warning); font-size: 11px; }
.skill-issues > div { display: grid; gap: 3px; }
.skill-issues strong { color: var(--kg-text-primary); font-size: 12px; }
.skill-issues span { overflow-wrap: anywhere; }
.extension-section { margin-top: var(--kg-space-6); }
.extensions-inner > .extension-section:first-child { margin-top: 0; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: var(--kg-space-5); margin-bottom: var(--kg-space-3); }
.section-head p { margin: 3px 0 0; color: var(--kg-text-tertiary); font-size: 12px; }
.section-meta { display: flex; align-items: center; gap: var(--kg-space-2); }
.section-meta :deep(.el-button) { gap: 5px; margin-left: 0; }
.section-count { color: var(--kg-text-tertiary); font-size: 12px; white-space: nowrap; }
.name-cell { min-width: 0; display: flex; align-items: center; gap: 9px; }
.name-cell > span:last-child { min-width: 0; display: grid; }
.name-cell strong { overflow: hidden; color: var(--kg-text-primary); font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.name-cell small { margin-top: 1px; color: var(--kg-text-tertiary); font: 10px/1.3 var(--kg-font-mono); }
.state-dot { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--kg-text-disabled); }
.state-dot.enabled { background: var(--kg-success); }
.command { display: block; overflow: hidden; color: var(--kg-text-secondary); font: 11px/1.45 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.connection-state { font-size: 11px; }
.connection-state.ok { color: var(--kg-success); }
.connection-state.failed { color: var(--kg-danger); }
.connection-state.pending { color: var(--kg-warning); }
.connection-state.unknown, .connection-state.muted, .muted { color: var(--kg-text-tertiary); }
.row-actions { display: flex; justify-content: center; white-space: nowrap; }
.row-actions :deep(.el-button + .el-button) { margin-left: 0; }
.row-actions :deep(.restricted-skill-action) { color: var(--kg-text-disabled); }
.row-actions :deep(.restricted-skill-action:hover),
.row-actions :deep(.restricted-skill-action:focus-visible) { color: var(--kg-text-tertiary); }
.loading-line { min-height: 78px; display: flex; align-items: center; gap: 9px; color: var(--kg-text-tertiary); font-size: 12px; }
.empty-state { min-height: 92px; display: flex; align-items: center; gap: 13px; padding: 15px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); background: var(--kg-bg-surface-1); }
.empty-mark { width: 38px; height: 38px; display: grid; flex: none; place-items: center; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); color: var(--kg-text-tertiary); }
.empty-state > div { min-width: 0; flex: 1; }
.empty-state strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 550; }
.empty-state p { margin: 3px 0 0; color: var(--kg-text-tertiary); font-size: 12px; }
.empty-state > :deep(.el-button) { gap: 5px; }
.skill-section { padding-bottom: var(--kg-space-7); }
.skill-mark { width: 28px; height: 28px; display: grid; flex: none; place-items: center; border-radius: var(--kg-radius-sm); background: var(--kg-accent-soft); color: var(--kg-accent); }
.skill-mark.large { width: 36px; height: 36px; }
.description, .tool-summary { display: block; overflow: hidden; color: var(--kg-text-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.missing-tools { display: block; margin-top: 2px; color: var(--kg-danger); font-size: 10px; }
.source-badge, .mode-badge { display: inline-flex; padding: 2px 6px; border-radius: var(--kg-radius-xs); background: var(--kg-bg-surface-3); color: var(--kg-text-secondary); font-size: 10px; }
:deep(.extension-edit-dialog) { display: flex; max-height: calc(100vh - 32px); flex-direction: column; }
:deep(.extension-edit-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; }
:deep(.extension-edit-dialog .el-dialog__footer) { flex: none; border-top: 1px solid var(--kg-border-subtle); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--kg-space-4); }
:deep(.el-form-item) { margin-bottom: 17px; }
:deep(.el-form-item__label) { margin-bottom: 6px; color: var(--kg-text-secondary); font-size: 12px; line-height: 18px; }
.field-note { margin-top: 5px; color: var(--kg-text-tertiary); font-size: 11px; }
.scope-warning { display: flex; align-items: flex-start; gap: 8px; margin: -4px 0 16px; padding: 9px 11px; border: 1px solid var(--kg-warning-border); border-radius: var(--kg-radius-sm); background: var(--kg-warning-soft); color: var(--kg-warning); font-size: 11px; line-height: 1.55; }
.import-warning { margin: 0 0 16px; }
.import-warning > div { display: grid; gap: 2px; }
.import-warning strong { color: var(--kg-text-primary); font-weight: 550; }
.import-intro { margin: 0 0 10px; color: var(--kg-text-secondary); font-size: 12px; line-height: 1.6; }
.import-intro code { color: var(--kg-accent); font-family: var(--kg-font-mono); }
.recommended-chip { display: inline-flex; align-items: center; margin-right: 7px; padding: 1px 6px; border-radius: var(--kg-radius-pill); background: var(--kg-accent-soft); color: var(--kg-accent); font-size: 10px; font-weight: 600; }
.import-note { margin-top: 12px; }
.import-choice { display: grid; gap: 7px; margin-top: 12px; padding: 10px 11px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-2); color: var(--kg-text-secondary); font-size: 11px; }
.hidden-file-input { display: none; }
.import-footer { width: 100%; display: grid; grid-template-columns: auto 1fr auto auto; gap: 8px; }
.import-secondary-actions { display: flex; align-items: center; gap: var(--kg-space-2); }
.import-secondary-actions :deep(.el-button) { margin-left: 0; }
.secret-clear-select { width: 100%; margin-top: 8px; }
.safety-note { display: flex; align-items: flex-start; gap: 8px; padding: 9px 11px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); background: var(--kg-bg-surface-2); color: var(--kg-text-secondary); font-size: 11px; line-height: 1.55; }
.safety-note :deep(.kg-icon) { margin-top: 1px; color: var(--kg-accent); }
.detail-head { display: flex; align-items: center; gap: 10px; }
.detail-head > div { display: grid; }
.detail-head strong { color: var(--kg-text-primary); font-size: 14px; }
.detail-head span { margin-top: 2px; color: var(--kg-text-tertiary); font: 11px/1.4 var(--kg-font-mono); }
.detail-description { margin: 15px 0; color: var(--kg-text-secondary); font-size: 12px; line-height: 1.65; }
.detail-list { margin: 0; border-top: 1px solid var(--kg-border-subtle); }
.detail-list > div { display: grid; grid-template-columns: 90px 1fr; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--kg-border-subtle); font-size: 12px; }
.detail-list dt { color: var(--kg-text-tertiary); }
.detail-list dd { margin: 0; color: var(--kg-text-secondary); }
.instructions { margin-top: 16px; }
.instructions > strong { color: var(--kg-text-primary); font-size: 12px; }
.instructions pre { max-height: 260px; margin: 7px 0 0; padding: 11px; overflow: auto; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-2); color: var(--kg-text-secondary); font: 11px/1.6 var(--kg-font-mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.detail-safety { margin-top: 14px; }
.mcp-policy-warning { margin: 10px 0 0; }
.mcp-tool-list { display: grid; gap: 10px; max-height: 430px; margin-top: 12px; overflow-y: auto; }
.mcp-tool-detail { padding: 10px 11px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-1); }
.mcp-tool-detail code { color: var(--kg-accent); font: 11px/1.4 var(--kg-font-mono); }
.mcp-tool-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mcp-tool-title code { min-width: 0; overflow-wrap: anywhere; }
.mcp-tool-title :deep(.el-select) { width: 190px; flex: none; }
.mcp-risk-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 12px; margin-top: 7px; }
.mcp-risk-summary > span { padding: 1px 6px; border-radius: var(--kg-radius-xs); font-size: 10px; }
.mcp-risk-summary .risk-low { background: var(--kg-success-soft); color: var(--kg-success); }
.mcp-risk-summary .risk-medium { background: var(--kg-warning-soft); color: var(--kg-warning); }
.mcp-risk-summary .risk-high { background: var(--kg-danger-soft); color: var(--kg-danger); }
.mcp-risk-summary small { color: var(--kg-text-tertiary); font-size: 10px; }
.mcp-tool-detail p { margin: 5px 0; color: var(--kg-text-secondary); font-size: 11px; line-height: 1.5; }
.mcp-tool-detail pre { max-height: 150px; margin: 6px 0 0; padding: 8px; overflow: auto; border-radius: var(--kg-radius-xs); background: var(--kg-bg-surface-2); color: var(--kg-text-tertiary); font: 10px/1.5 var(--kg-font-mono); }

@media (max-width: 900px) {
  .mcp-table :deep(.el-table__cell:nth-child(2)),
  .skill-table :deep(.el-table__cell:nth-child(2)),
  .skill-table :deep(.el-table__cell:nth-child(4)) { display: none; }
}
@media (max-width: 700px) {
  .section-head { align-items: stretch; flex-direction: column; gap: var(--kg-space-3); }
  .section-head p { display: none; }
  .section-meta { display: grid; width: 100%; grid-template-columns: 1fr auto; }
  .section-meta :deep(.el-button) { min-width: 132px; }
  .empty-state { align-items: flex-start; flex-wrap: wrap; }
  .empty-state > :deep(.el-button) { width: 100%; margin-left: 51px; }
  .import-footer { grid-template-columns: 1fr 1fr; }
  .import-footer > span { display: none; }
  .import-secondary-actions { grid-column: 1 / -1; }
  .import-secondary-actions :deep(.el-button:first-child) { flex: 1; }
  .mcp-table :deep(.el-table__cell:nth-child(3)),
  .mcp-table :deep(.el-table__cell:nth-child(4)),
  .skill-table :deep(.el-table__cell:nth-child(3)),
  .skill-table :deep(.el-table__cell:nth-child(5)) { display: none; }
  .form-grid { grid-template-columns: 1fr; gap: 0; }
}
</style>
