<template>
  <svg class="kg-logo" :width="size" :height="size" viewBox="0 0 24 24"
       fill="none" aria-hidden="true">
    <path d="M5.3 4.2 8.1 2.8 12 4.1l3.9-1.3 2.8 1.4v6.7c0 5-2.6 8.2-6.7 10.3-4.1-2.1-6.7-5.3-6.7-10.3V4.2Z"
          stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" />
    <path d="M9.2 8.1v7.4m0-3.6 5.3-4m-4.1 3.1 4.4 4.5"
          stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
          stroke-linejoin="round" />
    <circle cx="15.9" cy="16.8" r="1.15" fill="currentColor" />
  </svg>
</template>

<script setup>
defineProps({ size: { type: [Number, String], default: 24 } })
</script>

<style scoped>
.kg-logo { display: block; flex: none; }
</style>
