<template>
  <div class="md" v-html="html" @click="handleContentClick"></div>
</template>

<script setup>
import hljs from 'highlight.js/lib/common'
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

const defaultFence = md.renderer.rules.fence
md.renderer.rules.fence = (tokens, index, options, env, renderer) => {
  const token = tokens[index]
  const language = String(token.info || '').trim().split(/\s+/)[0]
  const label = md.utils.escapeHtml(language || 'CODE')
  const content = defaultFence(tokens, index, options, env, renderer)
  return `<div class="md-code-block"><div class="md-code-head"><span>${label}</span>`
    + '<button type="button" data-copy-code aria-label="复制代码" aria-live="polite">复制</button></div>'
    + `${content}</div>`
}

const html = computed(() => md.render(props.text || ''))

async function handleContentClick(event) {
  const button = event.target.closest?.('[data-copy-code]')
  if (!button) return
  const code = button.closest('.md-code-block')?.querySelector('code')?.textContent || ''
  if (!code) return
  try {
    await navigator.clipboard.writeText(code)
    button.setAttribute('aria-label', '代码已复制')
    button.textContent = '已复制'
    setTimeout(() => {
      button.setAttribute('aria-label', '复制代码')
      button.textContent = '复制'
    }, 1200)
  } catch {
    button.setAttribute('aria-label', '代码复制失败')
    button.textContent = '复制失败'
    setTimeout(() => {
      button.setAttribute('aria-label', '复制代码')
      button.textContent = '复制'
    }, 1200)
  }
}
</script>

<style>
.md { color: var(--kg-text-secondary); font-size: 14px; line-height: 1.78; word-break: break-word; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 8px 0; }
.md strong { color: var(--kg-text-primary); font-weight: 600; }
.md h1 { margin: 22px 0 10px; color: var(--kg-text-primary); font-size: 22px;
  font-weight: 650; line-height: 1.35; letter-spacing: 0; }
.md h3 { margin: 18px 0 7px; color: var(--kg-text-primary); font-size: 15px;
  font-weight: 600; line-height: 1.45; }
.md h2 { margin: 20px 0 8px; padding-top: 14px;
  border-top: 1px solid var(--kg-border-subtle); color: var(--kg-text-primary);
  font-size: 15px; font-weight: 600; line-height: 1.45; }
.md ul, .md ol { margin: 8px 0; padding-left: 23px; }
.md li { margin: 4px 0; padding-left: 2px; }
.md li::marker { color: var(--kg-accent); font-family: var(--kg-font-mono); font-size: .85em; }
.md code { padding: 2px 5px; border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-xs); background: var(--kg-bg-surface-2); color: var(--kg-accent);
  font-family: var(--kg-font-mono); font-size: .88em; }
.md pre { position: relative; margin: 11px 0; padding: 13px 14px; overflow-x: auto;
  border: 1px solid var(--kg-border-subtle); border-radius: var(--kg-radius-md);
  background: var(--kg-bg-code); box-shadow: inset 3px 0 0 #31548d; }
.md pre code { padding: 0; border: none; background: none; color: #d9e2ef;
  font-size: 12px; line-height: 1.65; }
.md table { display: block; width: 100%; margin: 11px 0; overflow-x: auto;
  border-collapse: collapse; border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md); }
.md thead { background: var(--kg-bg-surface-2); }
.md th, .md td { min-width: 96px; padding: 7px 10px; border-right: 1px solid var(--kg-border-subtle);
  border-bottom: 1px solid var(--kg-border-subtle); font-size: 12px; text-align: left; }
.md th { color: var(--kg-text-primary); font-size: 12px; font-weight: 600; }
.md tr:last-child td { border-bottom: none; }
.md th:last-child, .md td:last-child { border-right: none; }
.md blockquote { position: relative; margin: 11px 0; padding: 10px 12px 10px 38px;
  border: 1px solid var(--kg-info-border); border-radius: var(--kg-radius-md);
  background: var(--kg-info-soft); color: var(--kg-text-secondary); }
.md blockquote::before { content: 'i'; position: absolute; top: 11px; left: 13px;
  display: grid; place-items: center; width: 16px; height: 16px; border: 1px solid var(--kg-info);
  border-radius: 50%; color: var(--kg-info); font: 600 10px/1 var(--kg-font-mono); }
.md blockquote p { margin: 0; }
.md a { color: var(--kg-accent); text-decoration-color: var(--kg-border-strong);
  text-underline-offset: 3px; transition: color var(--kg-motion-fast); }
.md a:hover { color: var(--kg-accent-hover); }
.md hr { height: 1px; margin: 20px 0; border: none; background: var(--kg-border-subtle); }
.md img { max-width: 100%; border-radius: var(--kg-radius-md); }

.md-code-block { margin: 11px 0; }
.md-code-block .md-code-head {
  min-height: 30px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  border: 1px solid var(--kg-border-subtle);
  border-bottom: 0;
  border-radius: var(--kg-radius-md) var(--kg-radius-md) 0 0;
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
  font: 10px/1 var(--kg-font-mono);
}
.md-code-head button {
  border: 0;
  background: transparent;
  color: var(--kg-text-secondary);
  cursor: pointer;
  font-size: 11px;
}
.md-code-head button:hover { color: var(--kg-accent); }
.md-code-block pre { margin: 0; border-radius: 0 0 var(--kg-radius-md) var(--kg-radius-md); }

/* KylinGuard syntax palette: restrained semantic accents instead of a vendor theme. */
.md .hljs-comment, .md .hljs-quote { color: #7f8da4; font-style: italic; }
.md .hljs-keyword, .md .hljs-selector-tag, .md .hljs-literal,
.md .hljs-section, .md .hljs-link { color: var(--kg-info); }
.md .hljs-string, .md .hljs-title, .md .hljs-name, .md .hljs-type,
.md .hljs-attribute, .md .hljs-symbol, .md .hljs-bullet,
.md .hljs-addition, .md .hljs-variable, .md .hljs-template-tag,
.md .hljs-template-variable { color: var(--kg-success); }
.md .hljs-number, .md .hljs-meta, .md .hljs-built_in,
.md .hljs-builtin-name, .md .hljs-params { color: var(--kg-warning); }
.md .hljs-deletion, .md .hljs-selector-id, .md .hljs-selector-class,
.md .hljs-regexp { color: var(--kg-danger); }
.md .hljs-emphasis { font-style: italic; }
.md .hljs-strong { font-weight: 700; }
</style>
