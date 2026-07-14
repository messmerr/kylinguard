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
  gauge: ['M4 17a8 8 0 1 1 16 0', 'M12 13l3.5-3.5M7.5 17h9'],
  memory: ['M5 7h14v10H5z', 'M8 10h2v4H8zM14 10h2v4h-2z', 'M8 4v3M12 4v3M16 4v3M8 17v3M12 17v3M16 17v3'],
  service: ['M14.7 6.3a4 4 0 0 0-5 5L4 17l3 3 5.7-5.7a4 4 0 0 0 5-5l-2.6 2.6-3-3 2.6-2.6Z'],
  process: ['M5 6h14M5 12h10M5 18h7', 'M3 6h.01M3 12h.01M3 18h.01'],
  log: ['M6 3h8l4 4v14H6z', 'M14 3v5h5M9 12h6M9 16h6'],
  disk: ['M4 5h16v14H4z', 'M8 15h.01M12 15h4'],
  cpu: ['M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3'],
  model: ['M12 3 4.5 7.2v9.6L12 21l7.5-4.2V7.2L12 3Z', 'M8.5 9.5 12 11.4l3.5-1.9M12 11.4V16'],
  skill: ['M12 3l1.4 4.1L18 8.5l-3.6 2.7.2 4.5L12 13.2 9.4 15.7l.2-4.5L6 8.5l4.6-1.4L12 3Z', 'M18.5 15.5v5M16 18h5'],
  settings: ['M4 6h16M4 12h16M4 18h16', 'M8 4v4M16 10v4M10 16v4'],
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
