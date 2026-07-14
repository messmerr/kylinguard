<template>
  <div class="inline-mention-editor" :class="{ disabled }">
    <div
      ref="editorRef"
      class="inline-mention-input"
      :contenteditable="disabled ? 'false' : 'true'"
      :data-placeholder="placeholder"
      :aria-disabled="disabled"
      role="textbox"
      aria-multiline="true"
      spellcheck="true"
      @input="handleInput"
      @keydown="handleKeydown"
      @keyup="handleSelectionChange"
      @click="handleClick"
      @paste="handlePaste"
      @drop="handleDrop"
      @blur="handleBlur"
      @compositionstart="isComposing = true"
      @compositionend="handleCompositionEnd"
    ></div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { editorPlainText, normalizeEditorNodes } from '../utils/contextMention.js'

const props = defineProps({
  modelValue: { type: Array, default: () => [{ type: 'text', text: '' }] },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
})

const emit = defineEmits([
  'update:modelValue', 'query-change', 'mention-keydown', 'submit', 'escape',
])

const editorRef = ref(null)
const isComposing = ref(false)
let activeQueryRange = null
let activeQuery = null
let lastSignature = ''
let blurTimer = null
const trustedMentionElements = new WeakSet()

function signature(nodes) {
  return JSON.stringify(normalizeEditorNodes(nodes))
}

function createMentionElement(node) {
  const element = document.createElement('span')
  element.className = `inline-mention-token ${node.type}`
  element.contentEditable = 'false'
  element.draggable = false
  element.dataset.mentionType = node.type
  element.dataset.label = node.label
  if (node.type === 'skill') element.dataset.mentionId = node.id
  else {
    element.dataset.mentionPath = node.relativePath
    if (node.path) element.dataset.absolutePath = node.path
  }

  const label = document.createElement('span')
  label.className = 'inline-mention-label'
  label.textContent = `@${node.label}`
  element.append(label)

  const remove = document.createElement('button')
  remove.type = 'button'
  remove.tabIndex = -1
  remove.className = 'inline-mention-remove'
  remove.dataset.mentionRemove = 'true'
  remove.setAttribute('aria-label', `移除 ${node.label}`)
  remove.textContent = '×'
  element.append(remove)
  trustedMentionElements.add(element)
  return element
}

function renderNodes(nodes) {
  const editor = editorRef.value
  if (!editor) return
  const fragment = document.createDocumentFragment()
  for (const node of normalizeEditorNodes(nodes)) {
    if (node.type === 'text') fragment.append(document.createTextNode(node.text))
    else fragment.append(createMentionElement(node))
  }
  editor.replaceChildren(fragment)
  activeQueryRange = null
  activeQuery = null
}

function readNodes() {
  const result = []
  const editor = editorRef.value
  if (!editor) return [{ type: 'text', text: '' }]
  for (const child of editor.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) {
      result.push({ type: 'text', text: child.textContent || '' })
      continue
    }
    if (!(child instanceof HTMLElement)) continue
    const type = trustedMentionElements.has(child) ? child.dataset.mentionType : ''
    if (type === 'skill') {
      result.push({
        type, id: child.dataset.mentionId || '', label: child.dataset.label || '',
      })
    } else if (type === 'file') {
      result.push({
        type, relativePath: child.dataset.mentionPath || '',
        label: child.dataset.label || '',
        ...(child.dataset.absolutePath ? { path: child.dataset.absolutePath } : {}),
      })
    } else if (child.tagName === 'BR') {
      result.push({ type: 'text', text: '\n' })
    } else {
      // 浏览器扩展或输入法若临时包裹文本，只保留纯文本，不让任意 HTML 进入状态。
      result.push({ type: 'text', text: child.textContent || '' })
    }
  }
  return normalizeEditorNodes(result)
}

function publishNodes() {
  const nodes = readNodes()
  lastSignature = signature(nodes)
  emit('update:modelValue', nodes)
  return nodes
}

function selectionRange() {
  const editor = editorRef.value
  const selection = window.getSelection?.()
  if (!editor || !selection?.rangeCount) return null
  const range = selection.getRangeAt(0)
  if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null
  return range
}

function placeCaret(range) {
  const selection = window.getSelection?.()
  if (!selection) return
  selection.removeAllRanges()
  selection.addRange(range)
}

function placeCaretAtEnd() {
  const editor = editorRef.value
  if (!editor) return null
  const range = document.createRange()
  range.selectNodeContents(editor)
  range.collapse(false)
  placeCaret(range)
  return range
}

function textPosition(range, atStart = true) {
  const editor = editorRef.value
  if (!editor) return 0
  const prefix = document.createRange()
  prefix.selectNodeContents(editor)
  if (atStart) prefix.setEnd(range.startContainer, range.startOffset)
  else prefix.setEnd(range.endContainer, range.endOffset)
  return Array.from(prefix.toString()).length
}

function mapRunOffset(entries, offset) {
  let remaining = offset
  for (const entry of entries) {
    if (remaining <= entry.length) return { node: entry.node, offset: remaining }
    remaining -= entry.length
  }
  const last = entries.at(-1)
  return last ? { node: last.node, offset: last.length } : null
}

function queryAtCaret() {
  const editor = editorRef.value
  const range = selectionRange()
  if (!editor || !range?.collapsed) return null

  let caretNode = range.startContainer
  let caretOffset = range.startOffset
  if (caretNode === editor) {
    const previous = editor.childNodes[caretOffset - 1]
    if (previous?.nodeType !== Node.TEXT_NODE) return null
    caretNode = previous
    caretOffset = previous.textContent?.length || 0
  }
  if (caretNode.nodeType !== Node.TEXT_NODE || caretNode.parentNode !== editor) return null

  const children = [...editor.childNodes]
  const caretIndex = children.indexOf(caretNode)
  if (caretIndex < 0) return null
  let firstTextIndex = caretIndex
  while (firstTextIndex > 0 && children[firstTextIndex - 1].nodeType === Node.TEXT_NODE) {
    firstTextIndex -= 1
  }
  const entries = []
  let before = ''
  for (let index = firstTextIndex; index <= caretIndex; index += 1) {
    const node = children[index]
    const fullText = node.textContent || ''
    const text = index === caretIndex ? fullText.slice(0, caretOffset) : fullText
    entries.push({ node, length: text.length })
    before += text
  }
  const match = before.match(/(^|[\s\p{P}])@([^\s@]*)$/u)
  if (!match) return null
  const query = match[2]
  const localStart = before.length - query.length - 1
  const start = mapRunOffset(entries, localStart)
  if (!start) return null

  const queryRange = document.createRange()
  queryRange.setStart(start.node, start.offset)
  queryRange.setEnd(caretNode, caretOffset)
  return {
    query,
    domRange: queryRange,
    range: { start: textPosition(queryRange), end: textPosition(queryRange, false) },
    selection: { start: textPosition(range), end: textPosition(range, false) },
  }
}

function updateQuery() {
  if (isComposing.value) return
  const next = queryAtCaret()
  activeQueryRange = next?.domRange?.cloneRange() || null
  activeQuery = next ? { query: next.query, range: next.range, selection: next.selection } : null
  emit('query-change', activeQuery)
}

function insertText(text) {
  const editor = editorRef.value
  if (!editor) return
  const range = selectionRange() || placeCaretAtEnd()
  if (!range) return
  range.deleteContents()
  const node = document.createTextNode(String(text || ''))
  range.insertNode(node)
  range.setStartAfter(node)
  range.collapse(true)
  placeCaret(range)
}

function handleInput() {
  publishNodes()
  updateQuery()
}

function handleSelectionChange() {
  if (!isComposing.value) updateQuery()
}

function handleCompositionEnd() {
  isComposing.value = false
  publishNodes()
  updateQuery()
}

function handlePaste(event) {
  if (props.disabled) return
  event.preventDefault()
  const text = event.clipboardData?.getData('text/plain').replace(/\r\n?/g, '\n') || ''
  insertText(text)
  publishNodes()
  updateQuery()
}

function handleDrop(event) {
  if (props.disabled) return
  // 外部拖放可能携带任意 HTML 和伪造的 data-mention-* 属性；只接收纯文本。
  event.preventDefault()
  const text = event.dataTransfer?.getData('text/plain').replace(/\r\n?/g, '\n') || ''
  if (!text) return
  insertText(text)
  publishNodes()
  updateQuery()
}

function adjacentMention(range, direction) {
  const editor = editorRef.value
  if (!editor || !range?.collapsed) return null
  const container = range.startContainer
  const offset = range.startOffset
  if (container.nodeType === Node.TEXT_NODE) {
    if ((direction < 0 && offset > 0)
        || (direction > 0 && offset < (container.textContent?.length || 0))) return null
    let sibling = direction < 0 ? container.previousSibling : container.nextSibling
    while (sibling?.nodeType === Node.TEXT_NODE && !(sibling.textContent || '').length) {
      sibling = direction < 0 ? sibling.previousSibling : sibling.nextSibling
    }
    return sibling instanceof HTMLElement && sibling.dataset.mentionType ? sibling : null
  }
  if (container !== editor) return null
  const child = direction < 0 ? container.childNodes[offset - 1] : container.childNodes[offset]
  return child instanceof HTMLElement && child.dataset.mentionType ? child : null
}

function removeMentionElement(element) {
  const editor = editorRef.value
  if (!editor || !element?.parentNode) return
  const range = document.createRange()
  range.setStartBefore(element)
  range.collapse(true)
  element.remove()
  placeCaret(range)
  publishNodes()
  updateQuery()
}

function handleClick(event) {
  const remove = event.target.closest?.('[data-mention-remove]')
  if (remove) {
    event.preventDefault()
    removeMentionElement(remove.closest('[data-mention-type]'))
    return
  }
  nextTick(updateQuery)
}

function handleBlur() {
  if (blurTimer) clearTimeout(blurTimer)
  blurTimer = setTimeout(() => {
    blurTimer = null
    activeQueryRange = null
    activeQuery = null
    emit('query-change', null)
  }, 100)
}

function handleKeydown(event) {
  if (props.disabled || isComposing.value || event.isComposing || event.keyCode === 229) return
  if (activeQuery && ['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
    if (event.key === 'Enter' && event.shiftKey) {
      // Shift+Enter 始终是正文换行，即便当前正显示候选。
    } else {
      event.preventDefault()
      emit('mention-keydown', { key: event.key, ...activeQuery })
      return
    }
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) {
      insertText('\n')
      publishNodes()
      updateQuery()
    } else {
      emit('submit')
    }
    return
  }
  if (event.key === 'Escape') {
    emit('escape')
    return
  }
  if (event.key === 'Backspace' || event.key === 'Delete') {
    const range = selectionRange()
    const mention = adjacentMention(range, event.key === 'Backspace' ? -1 : 1)
    if (mention) {
      event.preventDefault()
      removeMentionElement(mention)
    }
  }
}

function focus() {
  if (props.disabled) return
  editorRef.value?.focus()
}

function insertTrigger() {
  if (props.disabled) return false
  if (blurTimer) clearTimeout(blurTimer)
  focus()
  let range = selectionRange()
  if (!range) range = placeCaretAtEnd()
  if (!range) return false
  const prefix = document.createRange()
  prefix.selectNodeContents(editorRef.value)
  prefix.setEnd(range.startContainer, range.startOffset)
  const previous = Array.from(prefix.toString()).at(-1) || ''
  insertText(previous && !/[\s\p{P}]/u.test(previous) ? ' @' : '@')
  publishNodes()
  updateQuery()
  return true
}

function insertMention(token) {
  const editor = editorRef.value
  if (props.disabled || !editor || !activeQueryRange) return false
  if (blurTimer) clearTimeout(blurTimer)
  const range = activeQueryRange.cloneRange()
  if (!editor.contains(range.commonAncestorContainer)) return false

  const after = document.createRange()
  after.setStart(range.endContainer, range.endOffset)
  after.setEndAfter(editor.lastChild || editor)
  const needsSpace = !/^[\s\p{P}]/u.test(after.toString())

  range.deleteContents()
  const element = createMentionElement(token)
  range.insertNode(element)
  range.setStartAfter(element)
  if (needsSpace) {
    const spacer = document.createTextNode(' ')
    range.insertNode(spacer)
    range.setStartAfter(spacer)
  }
  range.collapse(true)
  placeCaret(range)
  publishNodes()
  activeQueryRange = null
  activeQuery = null
  emit('query-change', null)
  focus()
  return true
}

function getNodes() {
  return readNodes()
}

function hasText() {
  return Boolean(editorPlainText(readNodes()).trim())
}

function clear() {
  const nodes = [{ type: 'text', text: '' }]
  renderNodes(nodes)
  lastSignature = signature(nodes)
  emit('update:modelValue', nodes)
  emit('query-change', null)
}

watch(() => props.modelValue, (nodes) => {
  const nextSignature = signature(nodes)
  if (nextSignature === lastSignature) return
  lastSignature = nextSignature
  renderNodes(nodes)
}, { deep: true })

onMounted(() => {
  lastSignature = signature(props.modelValue)
  renderNodes(props.modelValue)
})

defineExpose({ clear, focus, getNodes, hasText, insertMention, insertTrigger })
</script>

<style scoped>
.inline-mention-editor { min-height: 24px; }
.inline-mention-editor.disabled { cursor: not-allowed; opacity: .68; }

.inline-mention-input {
  max-height: 176px;
  min-height: 24px;
  overflow-y: auto;
  color: var(--kg-text-primary);
  font: 14px/24px var(--kg-font-ui);
  outline: none;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.inline-mention-input:empty::before {
  color: var(--kg-text-tertiary);
  content: attr(data-placeholder);
  pointer-events: none;
}

.inline-mention-input :deep(.inline-mention-token) {
  display: inline-flex;
  max-width: min(280px, 80%);
  height: 22px;
  align-items: center;
  gap: 2px;
  margin: 0 2px;
  padding: 0 2px 0 6px;
  border: 1px solid #c5d5ff;
  border-radius: 6px;
  background: var(--kg-accent-soft);
  color: var(--kg-accent);
  font-size: 12px;
  font-weight: 550;
  line-height: 20px;
  vertical-align: baseline;
}

.inline-mention-input :deep(.inline-mention-token.file) {
  border-color: var(--kg-border-default);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-secondary);
}

.inline-mention-input :deep(.inline-mention-label) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-mention-input :deep(.inline-mention-remove) {
  width: 17px;
  height: 17px;
  display: grid;
  flex: none;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: currentColor;
  font: 14px/1 var(--kg-font-ui);
  cursor: pointer;
  opacity: .66;
}

.inline-mention-input :deep(.inline-mention-remove:hover) {
  background: rgb(23 92 255 / 10%);
  opacity: 1;
}
</style>
