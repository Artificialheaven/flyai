import type { Message, Session } from './types.js'

/** 将线性消息列表规范化为单分支，保留消息 ID、内容及时间。 */
export const normalizeSession = (session: Session): Session =>
  session.rootMessageId !== undefined
    ? session
    : {
        ...session,
        rootMessageId: session.messages[0]?.id ?? null,
        messages: session.messages.map((message, index, messages) => ({
          ...message,
          parentId: messages[index - 1]?.id ?? null,
          selectedChildId: messages[index + 1]?.id,
        })),
      }

/** 展示内容与请求上下文共用当前选中的分支路径。 */
export const getVisibleMessages = (session: Session): Message[] => {
  if (session.rootMessageId === undefined) {
    return session.messages
  }
  const byId = new Map(session.messages.map((message) => [message.id, message]))
  const path: Message[] = []
  let id = session.rootMessageId
  while (id) {
    const message = byId.get(id)
    if (!message) {
      break
    }
    path.push(message)
    byId.delete(id)
    id = message.selectedChildId ?? null
  }
  return path
}

export type MessageVersions = { ids: string[]; index: number }

/** 一次遍历收集兄弟版本；版本列表未变化时复用旧对象与索引，避免流式正文更新重绘历史消息。 */
export const buildMessageVersions = (
  session: Session,
  previous: ReadonlyMap<string, MessageVersions>
): ReadonlyMap<string, MessageVersions> => {
  // 带服务端历史版本的消息由下面的 thread.edits 循环统一接管。
  // 若同时参与本地分支分组，分组循环会先写入一个不匹配的版本并置 unchanged=false，
  // 之后即使被覆盖，收敛标记也回不来——每次渲染都产出新 Map，MessageList 在渲染期
  // setState 会触发 React "Too many re-renders" 而整页白屏。
  const serverVersioned = new Set<string>()
  for (const message of session.messages) {
    const info = 'serverVersions' in message ? message.serverVersions : undefined
    if (info && info.count > 1) {
      serverVersioned.add(message.id)
    }
  }

  const groups = new Map<string | null | undefined, string[]>()
  for (const message of session.messages) {
    const key = session.rootMessageId === undefined ? message.id : message.parentId
    const ids = groups.get(key)
    if (ids) {
      ids.push(message.id)
    } else {
      groups.set(key, [message.id])
    }
  }

  const versions = new Map<string, MessageVersions>()
  let unchanged = previous.size === session.messages.length
  for (const ids of groups.values()) {
    const cachedIds = previous.get(ids[0])?.ids
    const stableIds =
      cachedIds?.length === ids.length && ids.every((id, index) => id === cachedIds[index])
        ? cachedIds
        : ids
    ids.forEach((id, index) => {
      if (serverVersioned.has(id)) {
        return
      }
      const cached = previous.get(id)
      const version =
        cached?.ids === stableIds && cached.index === index ? cached : { ids: stableIds, index }
      versions.set(id, version)
      if (version !== cached) {
        unchanged = false
      }
    })
  }

  // FlyAI 服务端历史版本（thread.edits）不是本地分支图的一部分：
  // 编码成 anchor~eN / anchor~rN，由 selectMessageVersion 解出锚点与序号。
  for (const message of session.messages) {
    const info = 'serverVersions' in message ? message.serverVersions : undefined
    if (!info || info.count <= 1) {
      continue
    }
    const marker = info.kind === 'edit' ? 'e' : 'r'
    const ids = Array.from(
      { length: info.count },
      (_, index) => `${info.anchor}~${marker}${index}`
    )
    const cached = previous.get(message.id)
    const stableIds =
      cached?.ids.length === ids.length && ids.every((id, index) => id === cached.ids[index])
        ? cached.ids
        : ids
    const version =
      cached?.ids === stableIds && cached.index === info.index
        ? cached
        : { ids: stableIds, index: info.index }
    versions.set(message.id, version)
    if (version !== cached) {
      unchanged = false
    }
  }
  return unchanged ? previous : versions
}

/**
 * 把本地乐观消息 id 换成服务端落盘 id，并同步分支图里的所有引用。
 *
 * 用于发送失败（供应商报错/断流）后仍能按服务端 id 撤回、编辑或重新生成：
 * 本地乐观消息在服务端只登记了用户消息，id 必须与它一致。
 */
export const rekeyMessage = (session: Session, fromId: string, toId: string): Session => {
  if (!fromId || !toId || fromId === toId) {
    return session
  }
  return {
    ...session,
    rootMessageId:
      session.rootMessageId === fromId ? toId : session.rootMessageId,
    messages: session.messages.map((message) => {
      let next = message
      if (message.id === fromId) {
        next = { ...next, id: toId }
      }
      if (message.parentId === fromId) {
        next = { ...next, parentId: toId }
      }
      if (message.selectedChildId === fromId) {
        next = { ...next, selectedChildId: toId }
      }
      return next
    }),
  }
}

const isFailedReply = (message: Message) =>
  message.role === 'assistant' && message.status === 'error'

/**
 * 刷新时把「失败轮」的本地气泡与服务端历史对齐。
 *
 * 后端只保存成功的助手消息：供应商报错后，本地会多出一条 error 气泡，
 * 而它的用户消息在服务端已落盘（id 不同）。若直接用 keepLocal 保留本地列表，
 * 撤回/重新生成会带着服务端不认识的 id 请求，永远 404。
 *
 * 仅在「本地去掉 error 气泡后」与服务端逐条 role（用户消息还要 content）一致，
 * 且两边都是线性列表时采纳：用服务端消息（权威 id）重建，再把 error 气泡插回
 * 原位置。任何不一致都返回 null，保持原有本地优先逻辑。
 */
export const reconcileFailedReplies = (
  local: Session,
  remote: Session
): Session | null => {
  if (remote.rootMessageId !== undefined || local.rootMessageId !== undefined) {
    return null
  }
  const kept = local.messages.filter((message) => !isFailedReply(message))
  if (
    kept.length === local.messages.length ||
    kept.length !== remote.messages.length
  ) {
    return null
  }
  // extras[i] = 对齐完 i 条正常消息之后要插回的失败气泡
  const extras: Message[][] = Array.from(
    { length: remote.messages.length + 1 },
    () => []
  )
  let keptIndex = 0
  for (const message of local.messages) {
    if (isFailedReply(message)) {
      extras[keptIndex].push(message)
      continue
    }
    const twin = remote.messages[keptIndex]
    if (!twin || twin.role !== message.role) {
      return null
    }
    if (
      message.role === 'user' &&
      twin.role === 'user' &&
      message.content !== twin.content
    ) {
      return null
    }
    keptIndex += 1
  }
  const messages: Message[] = []
  for (let index = 0; index <= remote.messages.length; index += 1) {
    if (index > 0) {
      messages.push({
        ...remote.messages[index - 1],
        parentId: messages[messages.length - 1]?.id ?? null,
      })
    }
    for (const extra of extras[index]) {
      messages.push({
        ...extra,
        parentId: messages[messages.length - 1]?.id ?? null,
      })
    }
  }
  return { ...remote, messages }
}

/** 新消息挂在指定父节点下，并选择新分支；原节点与其后续对话保持不变。 */
export const appendBranch = (
  original: Session,
  parentId: string | null,
  messages: Message[]
): Session => {
  const session = normalizeSession(original)
  const first = messages[0]
  if (!first) {
    return session
  }
  return {
    ...session,
    rootMessageId: parentId === null ? first.id : session.rootMessageId,
    messages: [
      ...session.messages.map((message) =>
        message.id === parentId ? { ...message, selectedChildId: first.id } : message
      ),
      ...messages.map((message, index) => ({
        ...message,
        parentId: messages[index - 1]?.id ?? parentId,
        selectedChildId: messages[index + 1]?.id,
      })),
    ],
  }
}

/** 仅允许切换当前路径上某一节点的兄弟版本，恢复该版本已选中的后续分支。 */
export const selectVersion = (original: Session, messageId: string): Session => {
  const session = normalizeSession(original)
  const message = session.messages.find((item) => item.id === messageId)
  if (!message || !getVisibleMessages(session).some((item) => item.parentId === message.parentId)) {
    return original
  }
  return {
    ...session,
    rootMessageId: message.parentId === null ? message.id : session.rootMessageId,
    messages: session.messages.map((item) =>
      item.id === message.parentId ? { ...item, selectedChildId: message.id } : item
    ),
  }
}
