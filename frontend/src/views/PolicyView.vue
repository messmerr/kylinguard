<template>
  <div class="policy-page">
    <div class="policy-inner">
      <h3>自定义安全策略</h3>
      <p class="hint">自定义黑名单与保护路径只会收紧安全边界；自定义只读白名单是显式放行决策，请确认命令确实无副作用。</p>

      <div class="add-row">
        <el-select v-model="form.kind" style="width: 150px">
          <el-option value="blacklist" label="黑名单（正则）" />
          <el-option value="readonly" label="只读白名单（命令名）" />
          <el-option value="protected" label="保护路径（前缀）" />
        </el-select>
        <el-input v-model="form.pattern" placeholder="模式，如 \bwipefs\b 或 /etc/kylin-release"
                  style="flex: 1" />
        <el-input v-model="form.note" placeholder="说明（可选）" style="width: 200px" />
        <el-button type="primary" @click="add">添加</el-button>
      </div>
      <div v-if="error" class="error">{{ error }}</div>

      <el-table :data="custom" size="small" class="table">
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="kindTag(row.kind)">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="pattern" label="模式">
          <template #default="{ row }"><code>{{ row.pattern }}</code></template>
        </el-table-column>
        <el-table-column prop="note" label="说明" width="220" />
        <el-table-column width="80">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="remove(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!custom.length" class="empty">暂无自定义策略</div>

      <h3 class="builtin-title">内置安全基线（代码级，只读）</h3>
      <el-collapse v-if="builtin">
        <el-collapse-item :title="`危险命令黑名单（${builtin.blacklist.length} 条）`">
          <p v-for="([p, label]) in builtin.blacklist" :key="p" class="rule-line">
            <code>{{ p }}</code><span class="dim"> — {{ label }}</span>
          </p>
        </el-collapse-item>
        <el-collapse-item :title="`提权/子 shell 执行器（${builtin.privilege_escalators.length} 个，直接拒绝）`">
          <code class="rule-line">{{ builtin.privilege_escalators.join('  ') }}</code>
        </el-collapse-item>
        <el-collapse-item :title="`保护路径（${builtin.protected_prefixes.length} 个，写操作拒绝）`">
          <code class="rule-line">{{ builtin.protected_prefixes.join('  ') }}</code>
        </el-collapse-item>
        <el-collapse-item :title="`只读白名单（${Object.keys(builtin.safe_commands).length} 个命令，含危险 flag 排除）`">
          <p v-for="(flags, cmd) in builtin.safe_commands" :key="cmd" class="rule-line">
            <code>{{ cmd }}</code>
            <span v-if="flags.length" class="dim"> — 禁用 flag: {{ flags.join(' ') }}</span>
          </p>
          <p class="rule-line dim">另有 systemctl 只读子命令：{{ builtin.systemctl_ro_subcmds.join(' ') }}</p>
        </el-collapse-item>
        <el-collapse-item title="shell 元字符检测与 sudoers 白名单">
          <p class="rule-line dim">元字符模式 <code>{{ builtin.metachars }}</code>：出现即拒绝（执行器不经 shell，出现即为逃逸信号）。</p>
          <p class="rule-line dim">sudoers 精确白名单在部署阶段（M3）随 install.sh 配置于目标机 /etc/sudoers.d/kylinguard，此处不可编辑。</p>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../composables/useAuth.js'

const custom = ref([])
const builtin = ref(null)
const error = ref('')
const form = reactive({ kind: 'blacklist', pattern: '', note: '' })

const kindLabel = (k) => ({ blacklist: '黑名单', readonly: '只读白名单',
                            protected: '保护路径' }[k] || k)
const kindTag = (k) => ({ blacklist: 'danger', readonly: 'success',
                          protected: 'warning' }[k] || 'info')

async function refresh() {
  const r = await apiFetch('/api/policies')
  const body = await r.json()
  custom.value = body.custom
  builtin.value = body.builtin
}

async function add() {
  error.value = ''
  if (!form.pattern.trim()) return
  const r = await apiFetch('/api/policies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
  })
  if (!r.ok) {
    error.value = (await r.json()).detail || '添加失败'
    return
  }
  form.pattern = ''
  form.note = ''
  await refresh()
}

async function remove(id) {
  await apiFetch(`/api/policies/${id}`, { method: 'DELETE' })
  await refresh()
}

onMounted(refresh)
</script>

<style scoped>
.policy-page { flex: 1; overflow-y: auto; }
.policy-inner { max-width: 900px; margin: 0 auto; padding: 20px 24px 40px; }
h3 { color: #e6edf3; font-size: 15px; margin: 6px 0 4px; }
.builtin-title { margin-top: 28px; }
.hint { color: #8b949e; font-size: 12px; margin: 0 0 14px; }
.add-row { display: flex; gap: 8px; margin-bottom: 8px; }
.error { color: #f85149; font-size: 12px; margin-bottom: 8px; }
.table { margin-top: 4px; }
.empty { color: #484f58; font-size: 12px; padding: 14px 0; text-align: center; }
.rule-line { margin: 4px 0; font-size: 12px; color: #c9d1d9; }
.rule-line code { font-size: 12px; }
.dim { color: #8b949e; }
</style>
