import {
  computed,
  onUnmounted,
  ref,
  watch,
} from 'vue'
import { apiFetch } from './useApi.js'
import { enabledSkills } from './useExtensions.js'
import {
  editorContextFiles,
  editorSkillIds,
  filterMentionSkills,
  normalizeContextFiles,
  serializeEditorSnapshot,
} from '../utils/contextMention.js'

function valueOf(value) {
  return value && typeof value === 'object' && 'value' in value ? value.value : value
}

export function useComposerMentions({ nodes, editorRef, workspaceRoot, disabled }) {
  const mention = ref(null)
  const fileResults = ref([])
  const filesLoading = ref(false)
  const filesError = ref('')
  const activeIndex = ref(0)
  let searchTimer = null
  let searchSerial = 0
  let dismissedMentionKey = ''

  const mentionOpen = computed(() => Boolean(mention.value))
  const mentionQuery = computed(() => mention.value?.query || '')
  const selectedSkillIds = computed(() => editorSkillIds(nodes.value))
  const selectedFiles = computed(() => editorContextFiles(nodes.value))
  const skillsLimitReached = computed(() => selectedSkillIds.value.length >= 4)
  const filesLimitReached = computed(() => selectedFiles.value.length >= 8)
  const skillCandidates = computed(() => {
    const selected = new Set(selectedSkillIds.value)
    return filterMentionSkills(enabledSkills.value, mentionQuery.value).map((skill) => {
      const duplicate = selected.has(skill.id)
      return {
        kind: 'skill', key: `skill:${skill.id}`, skill,
        title: skill.name, detail: duplicate
          ? `${skill.description || skill.id} · 已添加`
          : skillsLimitReached.value ? '本轮最多指定 4 个 Skill' : skill.description || skill.id,
        disabled: duplicate || skillsLimitReached.value,
      }
    })
  })
  const fileCandidates = computed(() => {
    const selected = new Set(selectedFiles.value.map((file) => file.relativePath))
    return fileResults.value.map((file) => {
      const duplicate = selected.has(file.relativePath)
      return {
        kind: 'file', key: `file:${file.relativePath}`, file,
        title: file.name,
        detail: duplicate
          ? `${file.relativePath} · 已添加`
          : filesLimitReached.value ? '本轮最多引用 8 个服务器文件' : file.relativePath,
        disabled: duplicate || filesLimitReached.value,
      }
    })
  })
  const allCandidates = computed(() => [...skillCandidates.value, ...fileCandidates.value])

  watch(() => valueOf(workspaceRoot), () => {
    if (mentionOpen.value) scheduleFileSearch()
  })
  watch(allCandidates, (candidates) => {
    if (!candidates.length) activeIndex.value = 0
    else if (activeIndex.value >= candidates.length) activeIndex.value = candidates.length - 1
    if (candidates[activeIndex.value]?.disabled) moveActive(1, true)
  })

  function mentionKey(value) {
    if (!value) return ''
    return [value.query || '', value.range?.start ?? '', value.range?.end ?? ''].join('\u0000')
  }

  function closeMention({ preserveDismissal = false } = {}) {
    if (!preserveDismissal) dismissedMentionKey = ''
    mention.value = null
    fileResults.value = []
    filesError.value = ''
    filesLoading.value = false
    activeIndex.value = 0
    searchSerial += 1
    if (searchTimer) clearTimeout(searchTimer)
    searchTimer = null
  }

  async function searchFiles(serial, query, root) {
    try {
      const params = new URLSearchParams({ q: query, root })
      const response = await apiFetch(`/api/context/files?${params.toString()}`)
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = typeof body.detail === 'string' ? body.detail : body.detail?.message
        throw new Error(detail || `HTTP ${response.status}`)
      }
      if (serial !== searchSerial || !mentionOpen.value) return
      fileResults.value = normalizeContextFiles(body.files, 30)
    } catch (error) {
      if (serial !== searchSerial || !mentionOpen.value) return
      fileResults.value = []
      filesError.value = error.message || '服务器文件检索失败'
    } finally {
      if (serial === searchSerial) filesLoading.value = false
    }
  }

  function scheduleFileSearch() {
    if (searchTimer) clearTimeout(searchTimer)
    const serial = ++searchSerial
    fileResults.value = []
    filesError.value = ''
    filesLoading.value = true
    const query = mentionQuery.value
    const root = String(valueOf(workspaceRoot) || '')
    searchTimer = setTimeout(() => searchFiles(serial, query, root), 120)
  }

  function handleQueryChange(next) {
    if (valueOf(disabled) || !next) {
      closeMention()
      return
    }
    const nextMentionKey = mentionKey(next)
    if (dismissedMentionKey && nextMentionKey === dismissedMentionKey) {
      closeMention({ preserveDismissal: true })
      return
    }
    dismissedMentionKey = ''
    const changed = !mention.value
      || mention.value.query !== next.query
      || mention.value.range?.start !== next.range?.start
      || mention.value.range?.end !== next.range?.end
    mention.value = next
    if (changed) {
      activeIndex.value = 0
      scheduleFileSearch()
    }
  }

  function chooseCandidate(candidate = allCandidates.value[activeIndex.value]) {
    if (!candidate || candidate.disabled || !mention.value || valueOf(disabled)) return false
    const token = candidate.kind === 'skill'
      ? { type: 'skill', id: candidate.skill.id, label: candidate.skill.name }
      : candidate.kind === 'file'
        ? {
          type: 'file', relativePath: candidate.file.relativePath,
          path: candidate.file.path, label: candidate.file.name,
        }
        : null
    if (!token || !editorRef.value?.insertMention?.(token)) return false
    closeMention()
    return true
  }

  function moveActive(delta, includeCurrent = false) {
    const count = allCandidates.value.length
    if (!count) return
    let next = includeCurrent ? activeIndex.value - delta : activeIndex.value
    for (let attempts = 0; attempts < count; attempts += 1) {
      next = (next + delta + count) % count
      if (!allCandidates.value[next]?.disabled) {
        activeIndex.value = next
        return
      }
    }
  }

  function handleMentionKeydown(payload) {
    const key = payload?.key || payload
    if (!mentionOpen.value) return false
    if (key === 'ArrowDown' || key === 'ArrowUp') {
      moveActive(key === 'ArrowDown' ? 1 : -1)
      return true
    }
    if (key === 'Enter' || key === 'Tab') return chooseCandidate()
    if (key === 'Escape') {
      dismissedMentionKey = mentionKey(mention.value)
      closeMention({ preserveDismissal: true })
      return true
    }
    return false
  }

  function insertTrigger() {
    if (valueOf(disabled)) return
    editorRef.value?.insertTrigger?.()
  }

  function takeDraftContext() {
    const snapshot = serializeEditorSnapshot(nodes.value)
    nodes.value = [{ type: 'text', text: '' }]
    editorRef.value?.clear?.()
    closeMention()
    return snapshot
  }

  onUnmounted(() => {
    if (searchTimer) clearTimeout(searchTimer)
    searchSerial += 1
  })

  return {
    activeIndex,
    allCandidates,
    chooseCandidate,
    closeMention,
    fileCandidates,
    filesError,
    filesLimitReached,
    filesLoading,
    handleMentionKeydown,
    handleQueryChange,
    insertTrigger,
    mentionOpen,
    mentionQuery,
    selectedFiles,
    selectedSkillIds,
    skillCandidates,
    skillsLimitReached,
    takeDraftContext,
  }
}
