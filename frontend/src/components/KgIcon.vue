<template>
  <svg class="kg-icon" :width="size" :height="size" viewBox="0 0 24 24"
       fill="none" aria-hidden="true">
    <path v-for="(d, i) in iconPaths" :key="i" :d="d" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
})

const PATHS = {
  plus: ['M12 5v14M5 12h14'],
  task: ['M8 6h11M8 12h11M8 18h7', 'M4 6h.01M4 12h.01M4 18h.01'],
  audit: ['M12 8v4l2.8 1.8', 'M20 12a8 8 0 1 1-2.34-5.66'],
  shield: ['M12 3 5.5 5.8v5.1c0 4.7 2.5 7.8 6.5 10.1 4-2.3 6.5-5.4 6.5-10.1V5.8L12 3Z'],
  dashboard: ['M4 4h6v6H4zM14 4h6v10h-6zM4 14h6v6H4zM14 18h6v2h-6z'],
  bell: ['M6.5 9.5a5.5 5.5 0 0 1 11 0c0 6 2.5 6 2.5 7H4c0-1 2.5-1 2.5-7ZM10 20h4'],
  logout: ['M10 5H5v14h5M14 8l4 4-4 4M9 12h9'],
  server: ['M5 4h14v6H5zM5 14h14v6H5z', 'M8 7h.01M8 17h.01M11 7h5M11 17h5'],
  chevron: ['m9 5 7 7-7 7'],
  check: ['m5 12 4 4L19 6'],
  close: ['M6 6l12 12M18 6 6 18'],
  warning: ['M12 4 3.5 19h17L12 4Z', 'M12 9v4M12 16.5h.01'],
  info: ['M12 8h.01M11 12h1v5h1', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z'],
  terminal: ['m5 7 4 5-4 5M11 17h8'],
  activity: ['M3 12h4l2-6 4 12 2-6h6'],
  disk: ['M4 5h16v14H4z', 'M8 15h.01M12 15h4'],
  cpu: ['M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3'],
  lock: ['M7 10V8a5 5 0 0 1 10 0v2M5 10h14v10H5z'],
  arrowUp: ['M12 19V5M6 11l6-6 6 6'],
  download: ['M12 3v12M7 10l5 5 5-5M4 20h16'],
  refresh: ['M20 7v5h-5M4 17v-5h5', 'M18.5 9A7 7 0 0 0 6 7M5.5 15A7 7 0 0 0 18 17'],
}

const iconPaths = computed(() => PATHS[props.name] || PATHS.info)
</script>

<style scoped>
.kg-icon {
  display: block;
  flex: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
