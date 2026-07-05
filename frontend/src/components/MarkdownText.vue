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

const SECTION_RULES = [
  { keywords: ['问题', '现象', '状态', '概况'], cls: 'sec-blue'   },
  { keywords: ['根因', '原因', '分析', '定位'], cls: 'sec-red'    },
  { keywords: ['处置', '操作', '执行', '步骤'], cls: 'sec-green'  },
  { keywords: ['建议', '后续', '预防', '监控'], cls: 'sec-yellow' },
]

function sectionClass(title) {
  const t = title.trim()
  for (const { keywords, cls } of SECTION_RULES) {
    if (keywords.some(k => t.includes(k))) return cls
  }
  return ''
}

const html = computed(() => {
  const rendered = md.render(props.text || '')
  return rendered.replace(/<h2>(.*?)<\/h2>/g, (_, title) => {
    const cls = sectionClass(title)
    return `<h2 class="${cls}">${title}</h2>`
  })
})
</script>

<style>
.md { line-height: 1.7; word-break: break-word; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 6px 0; }
.md h1, .md h3 { margin: 12px 0 6px; font-size: 1.05em; }
.md h2 {
  margin: 14px 0 6px; font-size: 0.9em; font-weight: 600;
  padding: 5px 10px; border-radius: 5px;
  border-left: 3px solid #30363d; background: #0d1117;
  color: #c9d1d9; letter-spacing: 0.02em;
}
.md h2.sec-blue   { border-left-color: #58a6ff; color: #79c0ff; }
.md h2.sec-red    { border-left-color: #f78166; color: #ffa198; }
.md h2.sec-green  { border-left-color: #3fb950; color: #56d364; }
.md h2.sec-yellow { border-left-color: #d29922; color: #e3b341; }
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
