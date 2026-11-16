/**
 * 群聊 @ 提及解析（对照旧版 chat-stream.js parseMentionTargets）：
 * 长名优先匹配、同名去重、按文中出现顺序返回座位 id。
 * 纯函数，不依赖 store，便于单测。
 */
export type MentionMember = {
  id: string
  /** 座位名（兜底候选） */
  name: string
  /** 显示名/别名候选（角色卡名优先），可给多个 */
  aliases?: string[]
}

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export const parseMentionTargets = (text: string, members: MentionMember[]): string[] => {
  if (!text || members.length === 0) {
    return []
  }
  const nameToId = new Map<string, string>()
  const names: string[] = []
  const remember = (name: string | null | undefined, id: string) => {
    const trimmed = (name || '').trim()
    if (!trimmed || nameToId.has(trimmed)) {
      return
    }
    nameToId.set(trimmed, id)
    names.push(trimmed)
  }
  for (const member of members) {
    for (const alias of member.aliases || []) {
      remember(alias, member.id)
    }
    remember(member.name, member.id)
  }
  if (names.length === 0) {
    return []
  }
  // 长名优先：避免「小爱」抢先匹配「小爱同学」
  names.sort((a, b) => b.length - a.length)
  const pattern = new RegExp(`@(${names.map(escapeRegExp).join('|')})`, 'g')
  const order: string[] = []
  const seen = new Set<string>()
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    const memberId = nameToId.get(match[1])
    if (memberId && !seen.has(memberId)) {
      seen.add(memberId)
      order.push(memberId)
    }
  }
  return order
}
