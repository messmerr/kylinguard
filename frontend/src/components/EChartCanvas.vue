<template>
  <section class="visual-canvas chart-canvas" :class="{ embedded }">
    <header class="canvas-head">
      <div>
        <span class="canvas-kicker">实时视图</span>
        <strong>{{ title }}</strong>
      </div>
      <button type="button" title="恢复图表" aria-label="恢复图表" @click="restore">
        <KgIcon name="refresh" :size="14" />
      </button>
    </header>
    <div ref="host" class="chart-host" role="img" :aria-label="title"
         :style="{ height: `${Number(height) || 320}px` }"></div>
    <p v-if="error" class="canvas-error">{{ error }}</p>
  </section>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import KgIcon from './KgIcon.vue'

const props = defineProps({
  option: { type: Object, required: true },
  title: { type: String, default: '交互数据视图' },
  height: { type: [Number, String], default: 320 },
  embedded: { type: Boolean, default: false },
})

const host = ref(null)
const error = ref('')
let chart = null
let observer = null
let echartsPromise = null

function loadECharts() {
  echartsPromise ||= import('echarts')
  return echartsPromise
}

function normalizedOption() {
  return {
    color: ['#175cff', '#25a776', '#e0a329', '#7b61d1', '#d94b64', '#4f94d4'],
    animationDuration: 420,
    textStyle: {
      color: '#465168',
      fontFamily: 'Inter, "Noto Sans SC", "Microsoft YaHei UI", sans-serif',
    },
    tooltip: { trigger: 'axis', confine: true },
    toolbox: {
      right: 8,
      feature: { dataView: { readOnly: true }, restore: {}, saveAsImage: {} },
    },
    ...props.option,
  }
}

async function render() {
  await nextTick()
  if (!host.value) return
  try {
    error.value = ''
    const echarts = await loadECharts()
    chart ||= echarts.init(host.value, null, { renderer: 'canvas' })
    chart.setOption(normalizedOption(), { notMerge: true })
    chart.resize({ height: Number(props.height) || undefined })
  } catch (reason) {
    error.value = reason?.message || '图表配置无法渲染'
  }
}

function restore() {
  chart?.dispatchAction({ type: 'restore' })
  render()
}

onMounted(() => {
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
  render()
})
watch(() => props.option, render, { deep: true })
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.visual-canvas {
  margin: 12px 0;
  overflow: hidden;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-lg);
  background: #fff;
  box-shadow: 0 5px 18px rgb(32 49 82 / 7%);
}
.visual-canvas.embedded { margin: 6px -8px 0; border: 0; box-shadow: none; }
.visual-canvas.embedded .canvas-head { padding-right: 8px; padding-left: 8px; }

.canvas-head {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 12px 8px 15px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.canvas-head > div { display: grid; gap: 1px; }
.canvas-kicker { color: var(--kg-accent); font: 600 9px/1.4 var(--kg-font-mono); }
.canvas-head strong { color: var(--kg-text-primary); font-size: 13px; font-weight: 600; }
.canvas-head button {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-sm);
  background: #fff;
  color: var(--kg-text-tertiary);
  cursor: pointer;
}
.canvas-head button:hover { border-color: #aec4f8; background: var(--kg-accent-soft); color: var(--kg-accent); }
.chart-host { width: 100%; min-height: 220px; }
.canvas-error { margin: 0; padding: 10px 14px; border-top: 1px solid var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); font-size: 12px; }
</style>
