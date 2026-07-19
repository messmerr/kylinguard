<template>
  <section class="flow-canvas">
    <header class="canvas-head">
      <div>
        <span class="canvas-kicker">决策链</span>
        <strong>{{ title }}</strong>
      </div>
      <div class="zoom-actions">
        <button type="button" title="缩小" aria-label="缩小" @click="zoomBy(-0.1)">-</button>
        <button type="button" title="恢复大小" aria-label="恢复大小" @click="zoom = 1">
          <KgIcon name="refresh" :size="13" />
        </button>
        <button type="button" title="放大" aria-label="放大" @click="zoomBy(0.1)">+</button>
      </div>
    </header>
    <div class="flow-viewport">
      <div class="flow-diagram" :style="{ transform: `scale(${zoom})` }" v-html="svg"></div>
    </div>
    <p v-if="error" class="canvas-error">{{ error }}</p>
  </section>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import KgIcon from './KgIcon.vue'

const props = defineProps({
  code: { type: String, required: true },
  title: { type: String, default: 'Agent 决策流程' },
})

const svg = ref('')
const error = ref('')
const zoom = ref(1)
let renderIndex = 0
let mermaidPromise = null

async function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: 'Inter, "Noto Sans SC", "Microsoft YaHei UI", sans-serif',
        themeVariables: {
          primaryColor: '#eaf1ff', primaryTextColor: '#172033', primaryBorderColor: '#8eaff8',
          lineColor: '#7b879d', secondaryColor: '#edf9f3', tertiaryColor: '#fff8e8',
          clusterBkg: '#f7f9fc', clusterBorder: '#d7deea', edgeLabelBackground: '#ffffff',
        },
        flowchart: { curve: 'basis', htmlLabels: false },
      })
      return mermaid
    })
  }
  return mermaidPromise
}

async function render() {
  const current = ++renderIndex
  try {
    error.value = ''
    const mermaid = await loadMermaid()
    const result = await mermaid.render(`kg-mermaid-${Date.now()}-${current}`, props.code)
    if (current === renderIndex) svg.value = result.svg
  } catch (reason) {
    if (current === renderIndex) {
      svg.value = ''
      error.value = reason?.message?.split('\n')[0] || '流程图语法无法渲染'
    }
  }
}

function zoomBy(delta) {
  zoom.value = Math.max(0.7, Math.min(1.5, Number((zoom.value + delta).toFixed(1))))
}

onMounted(render)
watch(() => props.code, render)
</script>

<style scoped>
.flow-canvas { margin: 12px 0; overflow: hidden; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-lg); background: var(--kg-bg-surface-1); box-shadow: var(--kg-shadow-sm); }
.canvas-head { min-height: 46px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 8px 12px 8px 16px; border-bottom: 1px solid var(--kg-border-subtle); }
.canvas-head > div:first-child { display: grid; gap: 1px; }
.canvas-kicker { color: var(--kg-accent); font: 600 9px/1.4 var(--kg-font-mono); }
.canvas-head strong { color: var(--kg-text-primary); font-size: 14px; font-weight: 600; letter-spacing: .005em; }
.zoom-actions { display: flex; gap: 4px; }
.zoom-actions button { width: 28px; height: 28px; display: grid; place-items: center; border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-sm); background: var(--kg-bg-surface-1); color: var(--kg-text-secondary); font-weight: 600; cursor: pointer; transition: color var(--kg-motion-fast), border-color var(--kg-motion-fast), background var(--kg-motion-fast); }
.zoom-actions button:hover { border-color: var(--kg-accent-bright); background: var(--kg-accent-soft); color: var(--kg-accent); }
.flow-viewport { min-height: 260px; padding: 24px; overflow: auto; background-image: linear-gradient(var(--kg-border-subtle) 1px, transparent 1px), linear-gradient(90deg, var(--kg-border-subtle) 1px, transparent 1px); background-size: 24px 24px; }
.flow-diagram { min-width: 620px; transform-origin: top center; transition: transform var(--kg-motion-base) var(--kg-ease-standard); }
.flow-diagram :deep(svg) { display: block; max-width: none; margin: 0 auto; }
.canvas-error { margin: 0; padding: 10px 14px; border-top: 1px solid var(--kg-danger-border); background: var(--kg-danger-soft); color: var(--kg-danger); font-size: 12px; }
@media (max-width: 720px) { .flow-viewport { padding: 14px; } .flow-diagram { min-width: 520px; } }
</style>
