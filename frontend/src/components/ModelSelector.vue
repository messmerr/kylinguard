<template>
  <el-popover
    v-model:visible="open"
    placement="top-start"
    trigger="click"
    :width="360"
    :disabled="disabled"
    popper-class="model-selector-popper"
  >
    <template #reference>
      <button
        type="button"
        class="model-trigger"
        :class="{ empty: !activeModel }"
        :disabled="disabled"
        :title="triggerTitle"
        aria-label="选择会话模型与推理强度"
        aria-haspopup="dialog"
        aria-controls="session-model-selector"
        :aria-expanded="open"
      >
        <KgIcon name="model" :size="13" />
        <span class="model-trigger-label">{{ activeModel?.model.label || '配置模型' }}</span>
        <span v-if="activeModel" class="effort-compact">
          {{ effortLabel(sessionModel.reasoningEffort) }}
        </span>
        <span v-if="sessionModel.saving" class="kg-spinner selector-spinner" aria-hidden="true"></span>
        <KgIcon v-else name="chevron" :size="11" class="selector-chevron" />
      </button>
    </template>

    <div id="session-model-selector" class="selector-panel" role="dialog" aria-label="会话模型设置">
      <template v-if="availableModelGroups.length">
        <div class="selector-head">
          <div>
            <strong>会话模型</strong>
            <span>修改后从下一轮开始生效</span>
          </div>
          <button type="button" class="settings-link" @click="openSettings">
            管理
          </button>
        </div>

        <el-input
          v-if="modelCount > 7"
          v-model="search"
          size="small"
          clearable
          placeholder="搜索模型"
          class="model-search"
        />

        <div class="model-list" role="listbox" aria-label="可用模型">
          <section v-for="group in filteredGroups" :key="group.id" class="model-group">
            <div class="provider-name">{{ group.name }}</div>
            <button
              v-for="model in group.models"
              :key="`${group.id}:${model.id}`"
              type="button"
              class="model-option"
              :class="{ selected: isSelected(group.id, model.id) }"
              :disabled="sessionModel.saving"
              role="option"
              :aria-selected="isSelected(group.id, model.id)"
              @click="chooseModel(group.id, model.id)"
            >
              <span class="model-option-copy">
                <strong>{{ model.label }}</strong>
                <code v-if="model.label !== model.id">{{ model.id }}</code>
              </span>
              <KgIcon v-if="isSelected(group.id, model.id)" name="check" :size="14" />
            </button>
          </section>
          <div v-if="!filteredGroups.length" class="selector-empty compact">
            没有匹配的模型
          </div>
        </div>

        <div v-if="activeModel" class="effort-section">
          <div class="effort-head">
            <strong>推理强度</strong>
            <span>{{ activeModel.model.supportedEfforts.length ? '速度与推理深度的平衡' : '该模型未声明可调档位' }}</span>
          </div>
          <div class="effort-options" role="radiogroup" aria-label="推理强度">
            <button
              v-for="effort in effortOptions"
              :key="effort"
              type="button"
              :class="{ selected: sessionModel.reasoningEffort === effort }"
              :disabled="sessionModel.saving"
              role="radio"
              :aria-checked="sessionModel.reasoningEffort === effort"
              @click="chooseEffort(effort)"
            >{{ effortLabel(effort) }}</button>
          </div>
        </div>
      </template>

      <div v-else class="selector-empty">
        <span class="empty-icon"><KgIcon name="model" :size="18" /></span>
        <strong>还没有可用模型</strong>
        <p>先添加 API 提供商与模型，再开始任务。</p>
        <el-button size="small" type="primary" @click="openSettings">配置模型服务</el-button>
      </div>
    </div>
  </el-popover>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  availableModelGroups,
  effortLabel,
  findModel,
  sessionModel,
  setActiveModel,
} from '../composables/useModels.js'
import KgIcon from './KgIcon.vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  hasHistory: { type: Boolean, default: false },
})
const emit = defineEmits(['configure'])

const open = ref(false)
const search = ref('')

const activeModel = computed(() => findModel(
  sessionModel.providerId,
  sessionModel.modelId,
))
const modelCount = computed(() => availableModelGroups.value.reduce(
  (total, group) => total + group.models.length, 0,
))
const effortOptions = computed(() => [
  'auto', ...(activeModel.value?.model.supportedEfforts || []),
])
const filteredGroups = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  if (!query) return availableModelGroups.value
  return availableModelGroups.value.map((group) => ({
    ...group,
    models: group.models.filter((model) => (
      `${model.label} ${model.id}`.toLocaleLowerCase().includes(query)
    )),
  })).filter((group) => group.models.length)
})
const triggerTitle = computed(() => activeModel.value
  ? `${activeModel.value.provider.name} · ${activeModel.value.model.label} · 推理强度${effortLabel(sessionModel.reasoningEffort)}`
  : '配置模型服务')

function isSelected(providerId, modelId) {
  return sessionModel.providerId === providerId && sessionModel.modelId === modelId
}

async function applySelection(next) {
  try {
    await setActiveModel(next)
  } catch (error) {
    ElMessage.error(error.message || '模型切换失败')
  }
}

async function chooseModel(providerId, modelId) {
  if (props.hasHistory && sessionModel.providerId
      && sessionModel.providerId !== providerId) {
    const target = findModel(providerId, modelId)
    try {
      await ElMessageBox.confirm(
        `下一轮会把当前任务的历史上下文发送给“${target?.provider.name || providerId}”。`,
        '切换 API 提供商',
        { confirmButtonText: '继续切换', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  await applySelection({
    providerId,
    modelId,
    reasoningEffort: sessionModel.reasoningEffort,
  })
}

async function chooseEffort(reasoningEffort) {
  await applySelection({
    providerId: sessionModel.providerId,
    modelId: sessionModel.modelId,
    reasoningEffort,
  })
}

function openSettings() {
  open.value = false
  emit('configure')
}
</script>

<style scoped>
.model-trigger {
  min-width: 0;
  max-width: 210px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 7px;
  border: 1px solid transparent;
  border-radius: var(--kg-radius-sm);
  background: transparent;
  color: var(--kg-text-secondary);
  font-size: 11px;
  cursor: pointer;
}

.model-trigger:hover:not(:disabled) {
  border-color: var(--kg-border-default);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-primary);
}

.model-trigger:disabled { color: var(--kg-text-disabled); cursor: not-allowed; }
.model-trigger.empty { color: var(--kg-warning); }
.model-trigger-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.effort-compact { flex: none; color: var(--kg-text-tertiary); }
.effort-compact::before { content: '·'; margin-right: 6px; }
.selector-chevron { flex: none; transform: rotate(90deg); color: var(--kg-text-tertiary); }
.selector-spinner { width: 12px; height: 12px; flex: none; }

.selector-panel { color: var(--kg-text-secondary); }
.selector-head,
.effort-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.selector-head strong,
.effort-head strong { display: block; color: var(--kg-text-primary); font-size: 12px; font-weight: 600; }
.selector-head span,
.effort-head span { display: block; margin-top: 2px; color: var(--kg-text-tertiary); font-size: 11px; }
.settings-link { padding: 2px 4px; border: 0; background: transparent; color: var(--kg-accent); font-size: 11px; cursor: pointer; }
.settings-link:hover { color: var(--kg-accent-hover); }
.model-search { margin-top: 11px; }
.model-list { max-height: 260px; margin: 10px -5px 0; padding: 0 5px; overflow-y: auto; }
.model-group + .model-group { margin-top: 10px; }
.provider-name { padding: 0 7px 4px; color: var(--kg-text-tertiary); font-size: 10px; font-weight: 600; letter-spacing: 0; text-transform: uppercase; }
.model-option { width: 100%; min-height: 38px; display: flex; align-items: center; gap: 8px; padding: 5px 8px; border: 0; border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-secondary); text-align: left; cursor: pointer; }
.model-option:hover:not(:disabled) { background: var(--kg-bg-surface-2); color: var(--kg-text-primary); }
.model-option.selected { background: var(--kg-selection); color: var(--kg-text-primary); }
.model-option.selected > :deep(.kg-icon) { color: var(--kg-accent); }
.model-option:disabled { color: var(--kg-text-disabled); cursor: wait; }
.model-option-copy { min-width: 0; display: grid; flex: 1; gap: 1px; }
.model-option-copy strong { overflow: hidden; font-size: 12px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.model-option-copy code { overflow: hidden; color: var(--kg-text-tertiary); font: 10px/1.4 var(--kg-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.effort-section { margin-top: 11px; padding-top: 11px; border-top: 1px solid var(--kg-border-subtle); }
.effort-options { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; }
.effort-options button { min-width: 42px; height: 26px; padding: 0 9px; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: transparent; color: var(--kg-text-tertiary); font-size: 11px; cursor: pointer; }
.effort-options button:hover:not(:disabled) { border-color: var(--kg-border-default); color: var(--kg-text-primary); }
.effort-options button.selected { border-color: var(--kg-accent-active); background: var(--kg-selection); color: var(--kg-accent); }
.effort-options button:disabled { color: var(--kg-text-disabled); cursor: wait; }
.selector-empty { display: grid; justify-items: center; padding: 16px 8px 8px; text-align: center; }
.selector-empty.compact { padding: 16px 8px; color: var(--kg-text-tertiary); font-size: 11px; }
.empty-icon { width: 34px; height: 34px; display: grid; place-items: center; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md); color: var(--kg-text-tertiary); }
.selector-empty strong { margin-top: 9px; color: var(--kg-text-primary); font-size: 12px; font-weight: 600; }
.selector-empty p { margin: 3px 0 11px; color: var(--kg-text-tertiary); font-size: 11px; }

@media (max-width: 720px) {
  .model-trigger { max-width: 132px; }
  .effort-compact { display: none; }
}
</style>
