import { cardDisplayName } from '../../stores/loreStore.js'
import type { Character } from '../../types/lore.js'
import type { RoomMember, Thread } from '../../types/session.js'

/** 座位显示名：库卡别名/名字优先，卡缺失时回退座位名（对照旧版 memberDisplayName） */
export const memberDisplayName = (member: RoomMember, characters: Character[]): string => {
  const card = member.character_id
    ? characters.find((item) => item.id === member.character_id) ?? null
    : null
  return card ? cardDisplayName(card) : member.name || '未命名'
}

/** 座位头像：会话副本的头像优先，其次库卡 */
export const memberAvatar = (member: RoomMember, characters: Character[]): string | null => {
  if (member.override && typeof member.override.avatar === 'string') {
    return member.override.avatar
  }
  const card = member.character_id
    ? characters.find((item) => item.id === member.character_id) ?? null
    : null
  return card?.avatar || null
}

/** 下拉框当前值若已不在选项里（卡被删）回退到空值，避免浏览器强选第一项 */
export const selectValue = (value: string | null | undefined, ids: string[]): string =>
  value && ids.includes(value) ? value : ''

export const memoryModeLabel = (mode: string): string => {
  if (mode === 'rw') {
    return '记忆簿·读写'
  }
  if (mode === 'ro') {
    return '记忆簿·只读'
  }
  return '不挂记忆簿'
}

/** 窗口显示名：自定义标题优先，其次按窗口类型兜底 */
export const threadLabel = (thread: Thread): string =>
  thread.title || (thread.kind === 'group' ? '群聊' : '单聊')