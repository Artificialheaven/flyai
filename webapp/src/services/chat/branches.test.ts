import { describe, expect, it } from 'vitest'
import { reconcileFailedReplies, rekeyMessage } from './branches.js'
import type { Message, Session } from './types.js'

const user = (id: string, parentId: string | null = null): Message =>
  ({ id, role: 'user', content: id, files: [], createdAt: 1, parentId }) as Message

const reply = (
  id: string,
  parentId: string,
  status: 'done' | 'error' = 'done'
): Message =>
  ({
    id,
    role: 'assistant',
    createdAt: 2,
    model: null,
    thinking: false,
    search: false,
    status,
    blocks: [],
    prefix: true,
    parentId,
  }) as Message

const session = (
  messages: Message[],
  root: string | null | undefined
): Session => ({
  id: 's1',
  title: 't',
  createdAt: 0,
  updatedAt: 0,
  messages,
  rootMessageId: root,
})

describe('rekeyMessage', () => {
  it('替换消息 id，并改写子消息的 parentId 与会话根节点', () => {
    const before = session([user('local'), reply('r1', 'local')], 'local')
    const after = rekeyMessage(before, 'local', 'server')
    expect(after.rootMessageId).toBe('server')
    expect(after.messages.map((message) => message.id)).toEqual(['server', 'r1'])
    expect(after.messages[1].parentId).toBe('server')
  })

  it('改写父消息的 selectedChildId，否则可见路径会断在旧 id 上', () => {
    const before = session(
      [
        { ...user('u0'), selectedChildId: 'local' } as Message,
        user('local', 'u0'),
        reply('r1', 'local'),
      ],
      'u0'
    )
    const after = rekeyMessage(before, 'local', 'server')
    expect(after.messages[0].selectedChildId).toBe('server')
    expect(after.messages[1].id).toBe('server')
    expect(after.messages[1].parentId).toBe('u0')
    expect(after.messages[2].parentId).toBe('server')
  })

  it('空 id 或相同 id 时原样返回', () => {
    const before = session([user('a')], 'a')
    expect(rekeyMessage(before, '', 'b')).toBe(before)
    expect(rekeyMessage(before, 'a', 'a')).toBe(before)
  })
})

describe('reconcileFailedReplies', () => {
  it('失败的那轮用服务端用户消息 id，error 气泡原位保留', () => {
    const local = session(
      [
        { ...user('a-local'), content: '第一问' } as Message,
        reply('ok', 'a-local'),
        { ...user('b-local'), content: '第二问' } as Message,
        reply('fail', 'b-local', 'error'),
      ],
      undefined
    )
    const remote = session(
      [
        { ...user('a-srv'), content: '第一问' } as Message,
        reply('ok-srv', 'a-srv'),
        { ...user('b-srv'), content: '第二问' } as Message,
      ],
      undefined
    )
    const merged = reconcileFailedReplies(local, remote)
    expect(merged).not.toBeNull()
    expect(merged?.messages.map((message) => message.id)).toEqual([
      'a-srv',
      'ok-srv',
      'b-srv',
      'fail',
    ])
    // 撤回/重发生效：失败轮的用户消息是服务端 id
    const failedUser = merged?.messages.find(
      (message) => message.role === 'user' && message.content === '第二问'
    )
    expect(failedUser?.id).toBe('b-srv')
  })

  it('用户内容对不上时放弃合并，避免把撤回锚到错误的轮次', () => {
    const local = session([user('local')], undefined)
    const remote = session(
      [{ ...user('srv'), content: '别的内容' } as Message],
      undefined
    )
    expect(reconcileFailedReplies(local, remote)).toBeNull()
  })

  it('没有失败气泡或存在其它多出来的消息时不处理', () => {
    const remote = session([user('a')], undefined)
    expect(
      reconcileFailedReplies(session([user('a')], undefined), remote)
    ).toBeNull()

    const streaming = session(
      [user('a'), reply('fail', 'a', 'error'), user('pending')],
      undefined
    )
    expect(reconcileFailedReplies(streaming, remote)).toBeNull()
  })

  it('分支图（rootMessageId 已定义）不做手术', () => {
    const local = session([user('a'), reply('fail', 'a', 'error')], 'a')
    const remote = session([user('a')], 'a')
    expect(reconcileFailedReplies(local, remote)).toBeNull()
  })
})
