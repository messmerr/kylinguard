const formatCount = (value) => new Intl.NumberFormat('zh-CN').format(value)

/**
 * 将 planning 阶段的机器状态转换成稳定、克制的用户提示。
 * 不读取后端 message、文件路径或生成正文，避免把模型参数泄露到活动卡。
 */
export function planningProgressText(activity) {
  if (!activity) return ''
  switch (activity.planningActivity) {
    case 'constructing_tool_call':
      return '正在组织下一步操作…'
    case 'preparing_file_path':
      return '正在准备文件写入…'
    case 'generating_file_content':
      return '正在生成文件内容…'
  }
  if (activity.state === 'generating_content') return '正在生成内容…'
  if (activity.state === 'constructing_tool_call') return '正在组织下一步操作…'
  return ''
}

export function planningProgressCount(activity) {
  if (!activity) return ''
  if (Number.isFinite(activity.generatedChars)) {
    return `已生成 ${formatCount(activity.generatedChars)} 字符`
  }
  if (Number.isFinite(activity.generatedBytes)) {
    return `已生成 ${formatCount(activity.generatedBytes)} 字节`
  }
  return ''
}
