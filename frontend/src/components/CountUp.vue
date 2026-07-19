<template>
  <span class="kg-count">{{ display }}</span>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

/* 数字滚动：值变化时从旧值平滑滚动到新值（easeOutExpo）。
   非数字内容（如「—」「不可用」）原样展示，不滚动。 */
const props = defineProps({
  value: { type: [Number, String], required: true },
  duration: { type: Number, default: 900 },
  suffix: { type: String, default: '' },
})

const reduced = typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const current = ref(0)
let raf = null

function easeOutExpo(t) {
  return t >= 1 ? 1 : 1 - 2 ** (-10 * t)
}

function play(from, to) {
  if (raf) cancelAnimationFrame(raf)
  if (reduced || props.duration <= 0) {
    current.value = to
    return
  }
  const start = performance.now()
  const tick = (now) => {
    const p = Math.min(1, (now - start) / props.duration)
    current.value = Math.round(from + (to - from) * easeOutExpo(p))
    if (p < 1) raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
}

const numericValue = computed(() => {
  const n = Number(props.value)
  return Number.isFinite(n) && props.value !== '' ? n : null
})

watch(numericValue, (to, from) => {
  if (to == null) return
  play(from ?? 0, to)
}, { immediate: true })

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
})

const display = computed(() => (
  numericValue.value == null ? props.value : `${current.value}${props.suffix}`
))
</script>

<style scoped>
.kg-count {
  font-variant-numeric: tabular-nums;
}
</style>
