import { apiUrl, authHeaders, readDetail, FlyApiError, request } from './client.js'
import type { MessageImage, SessionDetail, SessionSummary } from './types.js'

export type SessionPatch = {
  title?: string
  pinned?: boolean
  character_id?: string
  world_id?: string
  max_tokens?: number
  disabled_tools?: string[]
}

/** 轻量会话列表（后端按 mtime 倒序、pinned 置顶） */
export const listSessions = async (): Promise<SessionSummary[]> => {
  const data = await request<{ sessions: SessionSummary[] }>('/api/sessions')
  return data.sessions ?? []
}

/** 会话全文搜索：标题与消息正文，返回命中片段（不含消息体/图片） */
export type SessionSearchHit = SessionSummary & {
  match_count: number
  snippet: string
}

export const searchSessions = async (query: string): Promise<SessionSearchHit[]> => {
  const data = await request<{ query: string; results: SessionSearchHit[] }>(
    `/api/sessions/search?q=${encodeURIComponent(query)}`
  )
  return data.results ?? []
}

/** 传统简单入口：只建壳，首条消息时后端 bootstrap_room 补线程 */
export const createSession = async (title = ''): Promise<SessionSummary> =>
  request<SessionSummary>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })

export const patchSession = async (sid: string, patch: SessionPatch): Promise<SessionSummary> =>
  request<SessionSummary>(`/api/sessions/${encodeURIComponent(sid)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export const deleteSession = async (sid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' })
}

export const batchDeleteSessions = async (ids: string[]): Promise<number> => {
  const data = await request<{ ok: boolean; deleted?: number }>('/api/sessions/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return data.deleted ?? ids.length
}

export const clearSession = async (sid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}/clear`, { method: 'POST' })
}

/** 完整房间：members/threads/messages 都在这里，列表项拿不到 */
export const getSession = async (sid: string): Promise<SessionDetail> => {
  const detail = await request<SessionDetail>(`/api/sessions/${encodeURIComponent(sid)}`, {
    headers: { 'X-FlyAI-Client': '2' },
  })
  return hydrateCardBundle(detail)
}

const bundleCache = new Map<string, { scripts: unknown[]; truncated: boolean }>()

/** 会话响应去重后按 digest 拉取作者脚本；浏览器/内存缓存避免重复下载。 */
const hydrateCardBundle = async (detail: SessionDetail): Promise<SessionDetail> => {
  const author = detail.st?.author
  const url = author?.scripts_url
  if (!author || !url || (author.scripts && author.scripts.length > 0)) {
    return detail
  }
  const key = author.scripts_digest || url
  let cached = bundleCache.get(key)
  if (!cached) {
    const res = await fetch(apiUrl(url), { headers: authHeaders() })
    if (res.ok) {
      const data = (await res.json()) as { author_scripts?: unknown[]; truncated?: boolean }
      cached = { scripts: data.author_scripts || [], truncated: Boolean(data.truncated) }
      bundleCache.set(key, cached)
    }
  }
  if (cached) {
    author.scripts = cached.scripts as typeof author.scripts
    author.truncated = Boolean(author.truncated || cached.truncated)
  }
  return detail
}

/**
 * 条件拉取会话：带上一次的 ETag，304 时返回 null（调用方保留现有状态）。
 * 稳态下（会话没变）打开/刷新只传几百字节的 304 响应，而不是整个 JSON。
 */
const sessionEtags = new Map<string, string>()

export const getSessionConditional = async (
  sid: string,
  force = false
): Promise<SessionDetail | null> => {
  const etag = force ? '' : sessionEtags.get(sid) || ''
  const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(sid)}`), {
    headers: {
      ...authHeaders(),
      'X-FlyAI-Client': '2',
      ...(etag ? { 'If-None-Match': etag } : {}),
    },
  })
  if (res.status === 304) {
    return null
  }
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  const detail = await hydrateCardBundle((await res.json()) as SessionDetail)
  const next = res.headers.get('ETag')
  if (next) sessionEtags.set(sid, next)
  else sessionEtags.delete(sid)
  return detail
}

/** 删除/切换分支后让下一次拉取强制走全量 */
export const invalidateSessionCache = (sid: string): void => {
  sessionEtags.delete(sid)
}

export type RecallResult = {
  ok: boolean
  thread_id: string | null
  anchor: string
  tombstone: { content: string | null; images: MessageImage[] | null; name: string | null }
}

/** 撤回 mid 所在的一整轮：后端原位留下墓碑并返回被撤回内容（供重新编辑回填）。 */
export const deleteMessage = async (sid: string, mid: string): Promise<RecallResult> =>
  request<RecallResult>(
    `/api/sessions/${encodeURIComponent(sid)}/messages/${encodeURIComponent(mid)}`,
    { method: 'DELETE' }
  )

/** 切换某个锚点轮的历史 edit / reply 版本；返回更新后的线程 */
export const switchTurnVersion = async (
  rid: string,
  tid: string,
  anchor: string,
  index: { edit_index?: number; reply_index?: number }
): Promise<unknown> =>
  request<unknown>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}/turns/${encodeURIComponent(anchor)}`,
    { method: 'PATCH', body: JSON.stringify(index) }
  )
export type PromptArchiveCall = {
  kind: string
  messages: Array<{ role: string; content?: string; name?: string; tool_calls?: unknown }>
  tools?: Array<{ name?: string; description?: string }>
  params?: Record<string, unknown>
}

export type PromptArchive = {
  session_id: string
  anchor_id: string
  calls: PromptArchiveCall[]
}

/** 某条回复实际发给模型的 system/history/tools 快照 */
export const getMessagePrompt = async (sid: string, mid: string): Promise<PromptArchive> =>
  request<PromptArchive>(
    `/api/sessions/${encodeURIComponent(sid)}/messages/${encodeURIComponent(mid)}/prompt`
  )
