export function alertBadgeText(value) {
  const count = Number(value)
  if (!Number.isFinite(count) || count <= 0) return ''
  const wholeCount = Math.floor(count)
  if (wholeCount <= 0) return ''
  return wholeCount > 99 ? '99+' : String(wholeCount)
}

export function resolveRuleChannels(channelIds, channels) {
  const channelById = new Map(
    (Array.isArray(channels) ? channels : []).map(channel => [String(channel.id), channel]),
  )
  return (Array.isArray(channelIds) ? channelIds : [])
    .map(id => channelById.get(String(id)))
    .filter(Boolean)
}
