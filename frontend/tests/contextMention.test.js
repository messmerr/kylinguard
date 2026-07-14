import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addContextFile,
  contextFilePaths,
  editorSkillIds,
  filterMentionSkills,
  mentionAtCursor,
  nodesFromMessageAndMentions,
  normalizeContextFiles,
  removeMention,
  requestContextMentions,
  serializeEditorSnapshot,
} from '../src/utils/contextMention.js'

test('@ 查询支持行首、空白和标点，但不匹配邮箱', () => {
  assert.deepEqual(mentionAtCursor('@disk', 5), { start: 0, end: 5, query: 'disk' })
  assert.deepEqual(mentionAtCursor('检查（@日志', 6), { start: 3, end: 6, query: '日志' })
  assert.deepEqual(mentionAtCursor('第一行\n@conf\n第二行', 9), {
    start: 4, end: 9, query: 'conf',
  })
  assert.equal(mentionAtCursor('user@example.com', 16), null)
  assert.equal(mentionAtCursor('正文 @one more', 12), null)
})

test('选择候选只移除 @ 查询并保留正文与换行', () => {
  const text = '检查配置\n请参考 @conf 后继续'
  const mention = mentionAtCursor(text, '检查配置\n请参考 @conf'.length)
  const result = removeMention(text, mention)
  assert.equal(result.text, '检查配置\n请参考  后继续')
  assert.equal(result.cursor, '检查配置\n请参考 '.length)
})

test('Skill 候选只包含已启用且可用的匹配项', () => {
  const result = filterMentionSkills([
    { id: 'disk', name: '磁盘诊断', description: '检查空间', enabled: true, available: true },
    { id: 'logs', name: '日志排查', enabled: false, available: true },
    { id: 'service', name: '服务排查', enabled: true, available: false },
  ], '空间')
  assert.deepEqual(result.map((item) => item.id), ['disk'])
})

test('服务器文件按相对路径去重、限制八项且请求不携带绝对路径', () => {
  const files = normalizeContextFiles([
    { path: '/srv/work/logs/a.log', name: 'a.log', relative_path: 'logs/a.log' },
    { path: '/different/root/a.log', name: 'duplicate', relative_path: 'logs/a.log' },
    { relative_path: 'conf/app.yaml', name: 'app.yaml' },
    '/absolute/path/without-relative.txt',
    ...Array.from({ length: 10 }, (_, index) => ({
      path: `/srv/work/file-${index}.txt`, relative_path: `file-${index}.txt`,
    })),
  ])
  assert.equal(files.length, 8)
  assert.equal(files[0].path, '/srv/work/logs/a.log')
  assert.equal(files[1].path, 'conf/app.yaml')
  assert.deepEqual(contextFilePaths(files).slice(0, 2), ['logs/a.log', 'conf/app.yaml'])
  assert.equal(contextFilePaths(files).some((path) => path.startsWith('/')), false)

  const unchanged = addContextFile(files, {
    path: '/srv/work/logs/a.log', relative_path: 'logs/a.log',
  })
  assert.equal(unchanged.length, 8)
})

test('编辑器快照保留可见标签，并用 Unicode code point 生成有序 offset', () => {
  const snapshot = serializeEditorSnapshot([
    { type: 'text', text: '  请检查😀 ' },
    { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
    { type: 'text', text: ' 和 ' },
    {
      type: 'file', path: '/srv/work/logs/a.log',
      relativePath: 'logs/a.log', label: 'a.log',
    },
    { type: 'text', text: '  ' },
  ])

  assert.equal(snapshot.message, '请检查😀 @磁盘诊断 和 @a.log')
  assert.equal(snapshot.plainText, '请检查😀  和')
  assert.deepEqual(snapshot.skillIds, ['disk-diagnosis'])
  assert.deepEqual(snapshot.contextFiles.map((file) => file.relativePath), ['logs/a.log'])
  assert.deepEqual(requestContextMentions(snapshot.contextMentions), [
    { type: 'skill', offset: 5, skill_id: 'disk-diagnosis' },
    { type: 'file', offset: 13, path: 'logs/a.log' },
  ])
  assert.deepEqual(snapshot.contentNodes, [
    { type: 'text', text: '请检查😀 ' },
    { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
    { type: 'text', text: ' 和 ' },
    { type: 'file', relativePath: 'logs/a.log', label: 'a.log' },
  ])
})

test('服务端 context_mentions 可还原到正文任意位置，旧消息退化为纯文本', () => {
  assert.deepEqual(nodesFromMessageAndMentions('先看😀 @磁盘诊断 再看 @a.log', [
    { type: 'skill', offset: 4, skill_id: 'disk-diagnosis', name: '磁盘诊断' },
    { type: 'file', offset: 13, path: 'logs/a.log', name: 'a.log' },
  ]), [
    { type: 'text', text: '先看😀 ' },
    { type: 'skill', id: 'disk-diagnosis', label: '磁盘诊断' },
    { type: 'text', text: ' 再看 ' },
    { type: 'file', relativePath: 'logs/a.log', label: 'a.log' },
  ])
  assert.deepEqual(nodesFromMessageAndMentions('旧消息', []), [
    { type: 'text', text: '旧消息' },
  ])
})

test('Skill ID 去重并限制四项，只有标签的草稿不算正文', () => {
  const nodes = [
    { type: 'skill', id: 'one', label: '一' },
    { type: 'skill', id: 'one', label: '一' },
    ...['two', 'three', 'four', 'five'].map((id) => ({ type: 'skill', id, label: id })),
  ]
  assert.deepEqual(editorSkillIds(nodes), ['one', 'two', 'three', 'four'])
  assert.equal(serializeEditorSnapshot(nodes).plainText, '')
})
