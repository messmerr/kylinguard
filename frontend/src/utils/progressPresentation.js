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
      return '正在确认文件位置…'
    case 'generating_file_content':
      return '正在拟定文件内容…'
  }
  if (activity.state === 'generating_content') return '正在拟定内容…'
  if (activity.state === 'constructing_tool_call') return '正在组织下一步操作…'
  return ''
}

export function planningProgressCount(activity) {
  if (!activity) return ''
  if (Number.isFinite(activity.generatedChars)) {
    return `规划内容已处理 ${formatCount(activity.generatedChars)} 字符`
  }
  if (Number.isFinite(activity.generatedBytes)) {
    return `规划内容已处理 ${formatCount(activity.generatedBytes)} 字节`
  }
  return ''
}

/**
 * 把内部 stage/state 翻译成用户正在获得的进展，而不是底层传输动作。
 * `connecting` 只表示一次调用已经开始、尚未拿到首个可用输出；这段时间
 * 通常包含模型排队和推理，不能直接展示成“正在连接”。
 */
export function turnProgressText(turn, activity) {
  if (!turn) return ''
  if (turn.status === 'cancelling') return '正在结束本轮处理…'
  if (turn.status === 'cancelled') return '已停止接收后续结果'
  if (turn.status === 'waiting_user') return '需要你确认后继续'

  const stage = activity?.stage || turn.stage
  const state = activity?.state
  if (state === 'retry_wait') {
    if (stage === 'planning') return '模型请求暂时受阻，正在自动重试…'
    if (stage === 'reviewing') return '安全复核暂时受阻，正在自动重试…'
    return '当前操作暂时受阻，正在自动重试…'
  }
  if (state === 'failed') {
    if (stage === 'reviewing') return '在线安全复核未完成，已采用保守判定'
    return stage === 'executing'
      ? '操作执行失败，正在汇总结果…'
      : '本次规划未能完成，正在整理原因…'
  }
  if (state === 'completed') {
    if (stage === 'planning') return '已收到模型结果，正在整理方案…'
    if (stage === 'reviewing') return '已收到安全复核结果，正在确认执行条件…'
    if (stage === 'executing') return '操作完成，正在汇总结果…'
    if (stage === 'confirmation') return '已收到你的选择，正在继续任务…'
  }
  if (stage === 'confirmation' && ['cancelled', 'timed_out'].includes(state)) {
    return '确认已结束，正在更新任务状态…'
  }
  if (stage === 'planning') {
    return planningProgressText(activity)
      || (state === 'streaming'
        ? '正在梳理思路并形成方案…'
        : '规划模型正在理解请求并思考下一步…')
  }
  if (stage === 'reviewing') return '安全模型正在独立检查操作风险…'
  if (stage === 'executing') return '正在执行操作…'
  if (stage === 'confirmation') return '需要你确认后继续'
  if (stage === 'queued') return '上一轮仍在处理，本轮已排队…'
  if (stage === 'accepting' || stage === 'request' || !stage) {
    return '正在读取任务上下文与系统状态…'
  }
  return '正在推进任务…'
}
