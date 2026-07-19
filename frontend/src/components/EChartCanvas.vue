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
  const { tooltip: optionTooltip, ...rest } = props.option
  return {
    color: ['#175cff', '#25a776', '#e0a329', '#7b61d1', '#d94b64', '#4f94d4'],
    // 入场动画：expo 急停缓动 + 系列错峰；数据更新走更短的过渡
    animation: true,
    animationThreshold: 4000,
    animationDuration: 700,
    animationEasing: 'exponentialOut',
    animationDelay: (idx) => idx * 70,
    animationDurationUpdate: 420,
    animationEasingUpdate: 'cubicOut',
    animationDelayUpdate: (idx) => idx * 40,
    textStyle: {
      color: '#465168',
      fontFamily: 'Inter, "Noto Sans SC", "Microsoft YaHei UI", sans-serif',
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: '#ffffff',
      borderColor: '#e6ebf3',
      borderWidth: 1,
      padding: [9, 13],
      textStyle: { color: '#172033', fontSize: 12 },
      extraCssText:
        'box-shadow: 0 10px 28px rgb(23 43 84 / 13%); border-radius: 9px;',
      ...(optionTooltip || {}),
    },
    ...rest,
  }
}

async function render() {
  await nextTick()
  if (!host.value) return
  try {
    error.value = ''
    const echarts = await loadECharts()
    chart ||= echarts.init(host.value, null, {
      renderer: 'canvas',
      height: Number(props.height) || undefined,
    })
    // 注意：setOption 之后不能紧跟 resize()，
    // ECharts 6 中 resize 会立即终止入场动画。
    chart.setOption(normalizedOption(), { notMerge: true })
  } catch (reason) {
    error.value = reason?.message || '图表配置无法渲染'
  }
}

onMounted(() => {
  // ResizeObserver 在开始观察时会立刻回调一次，那次的 resize 同样会
  // 打断入场动画，跳过；之后尺寸真实变化时才 resize。
  let first = true
  observer = new ResizeObserver(() => {
    if (first) {
      first = false
      return
    }
    chart?.resize()
  })
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
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-lg);
  background: #fff;
  box-shadow: var(--kg-shadow-sm);
}
.visual-canvas.embedded { margin: 6px -8px 0; border: 0; box-shadow: none; }
.visual-canvas.embedded .canvas-head {
  min-height: 0;
  padding: 0 8px;
  border-bottom: 0;
}

.canvas-head {
  min-height: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 12px 8px 16px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.canvas-head strong {
  color: var(--kg-text-primary);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: .005em;
}
.chart-host { width: 100%; min-height: 220px; }
.canvas-error { margin: 0; padding: 10px 14px; border-top: 1px solid var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); font-size: 12px; }
</style>
