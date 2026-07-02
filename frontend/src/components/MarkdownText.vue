<template>
  <div class="md" v-html="html"></div>
</template>

<script setup>
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github-dark.css'
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps({ text: { type: String, default: '' } })

const md = new MarkdownIt({
  linkify: true,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value
      } catch { /* 回退到转义 */ }
    }
    return ''
  },
})

const html = computed(() => md.render(props.text || ''))
</script>

<style>
.md { line-height: 1.7; word-break: break-word; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 6px 0; }
.md h1, .md h2, .md h3 { margin: 12px 0 6px; font-size: 1.05em; }
.md ul, .md ol { padding-left: 22px; margin: 6px 0; }
.md li { margin: 2px 0; }
.md code { background: #1c2128; border-radius: 4px; padding: 1px 5px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.9em; }
.md pre { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
  padding: 10px 12px; overflow-x: auto; margin: 8px 0; }
.md pre code { background: none; padding: 0; font-size: 12px; }
.md table { border-collapse: collapse; margin: 8px 0; }
.md th, .md td { border: 1px solid #30363d; padding: 4px 10px; font-size: 13px; }
.md blockquote { border-left: 3px solid #30363d; margin: 8px 0;
  padding: 0 12px; color: #8b949e; }
.md a { color: #58a6ff; }
</style>
