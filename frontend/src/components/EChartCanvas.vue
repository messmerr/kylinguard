<template>
  <section class="visual-canvas chart-canvas" :class="{ embedded }">
    <header class="canvas-head">
      <strong>{{ title }}</strong>
    </header>
    <div ref="host" class="chart-host" role="img" :aria-label="title"
         :style="{ height: `${Number(height) || 320}px` }"></div>
    <p v-if="error" class="canvas-error">{{ error }}</p>
  </section>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

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
.visual-canvas.embedded .canvas-head {
  min-height: 0;
  padding: 0 8px;
  border-bottom: 0;
}

.canvas-head {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 12px 8px 15px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.canvas-head strong { color: var(--kg-text-primary); font-size: 16px; font-weight: 600; }
.chart-host { width: 100%; min-height: 220px; }
.canvas-error { margin: 0; padding: 10px 14px; border-top: 1px solid var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); font-size: 12px; }
</style>
