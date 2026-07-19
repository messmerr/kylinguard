<template>
  <div class="rich-message">
    <template v-for="segment in segments" :key="segment.key">
      <MarkdownText v-if="segment.type === 'markdown'" :text="segment.content" />
      <EChartCanvas
        v-else-if="segment.type === 'echarts' && segment.option"
        :option="segment.option"
        :title="segment.title"
      />
      <MermaidCanvas
        v-else-if="segment.type === 'mermaid'"
        :code="segment.content"
        :title="segment.title"
      />
      <div v-else class="visual-error">
        <strong>可视化配置无法解析</strong>
        <span>{{ segment.error }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import EChartCanvas from './EChartCanvas.vue'
import MarkdownText from './MarkdownText.vue'
import MermaidCanvas from './MermaidCanvas.vue'
import { parseRichMessage } from '../utils/richMessage.js'

const props = defineProps({ text: { type: String, default: '' } })
const segments = computed(() => parseRichMessage(props.text))
</script>

<style scoped>
.rich-message { min-width: 0; }
/* 分段之间留基础呼吸间距，图表/流程图卡片自带 12px 外边距会叠加 */
.rich-message > * + * { margin-top: var(--kg-space-2); }
.visual-error { display: grid; gap: 3px; margin: 12px 0; padding: 11px 13px; border: 1px solid var(--kg-danger-border); border-radius: var(--kg-radius-md); background: var(--kg-danger-soft); }
.visual-error strong { color: var(--kg-danger); font-size: 12px; }
.visual-error span { color: var(--kg-text-secondary); font: 11px/1.5 var(--kg-font-mono); }
</style>
