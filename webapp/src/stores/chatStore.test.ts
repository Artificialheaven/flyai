import { describe, expect, it } from 'vitest'
import { buildMentionMembers } from './chatStore.js'
import type { RoomMember, SessionDetail, Thread } from '../types/session.js'
import { emptyCharacter } from '../types/lore.js'

const member = (over: Partial<RoomMember>): RoomMember => ({
  id: 'm?',
  name: '座位',
  character_id: 'c1',
  is_user: false,
  ...over,
})

const detail = {
  members: [
    member({ id: 'u1', name: '我', is_user: true }),
    member({ id: 'a1', name: '座位一' }),
    member({ id: 'a2', name: '座位二', override: { ...emptyCharacter(), id: 'c2', name: '阿绫' } }),
    member({ id: 'a3', name: '座位三' }),
  ],
} as SessionDetail

const groupThread = {
  id: 't1',
  kind: 'group',
  member_ids: ['u1', 'a1', 'a2'],
} as Thread

const dmThread = { id: 't0', kind: 'dm', member_ids: ['u1', 'a1'] } as Thread

describe('buildMentionMembers', () => {
  it('群聊只取窗口内非用户座位，override 名作为别名', () => {
    expect(buildMentionMembers(detail, groupThread)).toEqual([
      { id: 'a1', name: '座位一', aliases: [] },
      { id: 'a2', name: '座位二', aliases: ['阿绫'] },
    ])
  })

  it('单聊窗口没有 @ 候选', () => {
    expect(buildMentionMembers(detail, dmThread)).toEqual([])
    expect(buildMentionMembers(detail, undefined)).toEqual([])
  })
})
