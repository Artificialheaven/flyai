import { request } from './client.js'

/** P3 主动行为接口（/api/proactor），字段与 routes_proactor.py 原样对齐。 */

export type MoodView = {
  mood: number
  dims: Record<string, number>
  announce: string
  announce_text?: string
  row_id: number
}

export type MoodTimelineRow = {
  id: number
  session_id: string
  character_id: string
  delta: number
  /** JSON 字符串（后端原样返回） */
  dims: string
  reason: string
  confidence: number
  source: string
  created_at: number
}

export type SelfMemoryRow = {
  id: number
  statement: string
  /** 逗号分隔的标签串 */
  tags: string
  importance: number
  superseded: number
  supersedes_id: number
  created_at: number
  last_accessed: number
  access_count: number
}

export type SelfMemoryTrace = {
  id: number
  kind: string
  memory_id: number
  detail: string
  created_at: number
}

export type Commitment = {
  id: number
  session_id: string
  owner_name: string
  content: string
  due_at: number
  status: 'open' | 'closed' | 'cancelled'
  reminded: number
  created_at: number
  closed_at: number
}

export type Job = {
  id: number
  session_id: string
  mode: 'once' | 'daily' | 'weekly'
  hhmm: string
  weekday: number
  brief: string
  enabled: number
  next_run_at: number
  next_run_local: string
  last_run_at: number
  last_status: string
  created_at: number
}

export type Poll = {
  id: number
  session_id: string
  title: string
  probability: number
  wake_every_n: number
  active_hours: number
  ticks_since_fire: number
  enabled: number
  created_at: number
}

export type ProactiveState = {
  session_id: string
  enabled: number
  wake_miss_streak: number
  last_wake_at: number
  last_speakup_at: number
  last_human_at: number
  updated_at: number
}

/** ---------- 心情 ---------- */

export const getMood = (sessionId: string, characterId: string) =>
  request<MoodView>(
    `/api/proactor/mood/view?session_id=${encodeURIComponent(sessionId)}` +
      `&character_id=${encodeURIComponent(characterId)}`
  )

export const getMoodTimeline = (sessionId: string, characterId: string, limit = 100) =>
  request<{ items: MoodTimelineRow[] }>(
    `/api/proactor/mood/timeline?session_id=${encodeURIComponent(sessionId)}` +
      `&character_id=${encodeURIComponent(characterId)}&limit=${limit}`
  )

export const clearMood = (sessionId: string, characterId: string) =>
  request<{ ok: boolean }>('/api/proactor/mood/clear', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, character_id: characterId }),
  })

/** ---------- 自我记忆 ---------- */

export const listSelfMemory = (includeSuperseded = false) =>
  request<{ items: SelfMemoryRow[] }>(
    `/api/proactor/self-memory?include_superseded=${includeSuperseded ? 'true' : 'false'}`
  )

export const selfMemoryTraces = (limit = 100) =>
  request<{ items: SelfMemoryTrace[] }>(`/api/proactor/self-memory/traces?limit=${limit}`)

export const addSelfMemory = (statement: string, tags: string[], importance: number) =>
  request<{ ok: boolean; item: SelfMemoryRow; created: boolean }>(
    '/api/proactor/self-memory',
    {
      method: 'POST',
      body: JSON.stringify({ statement, tags, importance }),
    }
  )

export const supersedeSelfMemory = (id: number, statement: string) =>
  request<{ ok: boolean; item: SelfMemoryRow }>(
    `/api/proactor/self-memory/${id}/supersede`,
    { method: 'POST', body: JSON.stringify({ statement }) }
  )

export const deleteSelfMemory = (id: number) =>
  request<{ ok: boolean }>(`/api/proactor/self-memory/${id}`, { method: 'DELETE' })

/** ---------- 承诺 ---------- */

export const listCommitments = (params: { sessionId?: string; status?: string } = {}) => {
  const query = new URLSearchParams()
  if (params.sessionId) {
    query.set('session_id', params.sessionId)
  }
  if (params.status) {
    query.set('status', params.status)
  }
  return request<{ items: Commitment[] }>(`/api/proactor/commitments?${query.toString()}`)
}

export const addCommitment = (input: {
  sessionId: string
  content: string
  dueAt: string
  ownerName?: string
}) =>
  request<{ ok: boolean; item: Commitment }>('/api/proactor/commitments', {
    method: 'POST',
    body: JSON.stringify({
      session_id: input.sessionId,
      content: input.content,
      due_at: input.dueAt,
      owner_name: input.ownerName ?? '',
    }),
  })

export const closeCommitment = (id: number) =>
  request<{ ok: boolean }>(`/api/proactor/commitments/${id}/close`, {
    method: 'POST',
    body: JSON.stringify({}),
  })

export const cancelCommitment = (id: number) =>
  request<{ ok: boolean }>(`/api/proactor/commitments/${id}/cancel`, {
    method: 'POST',
    body: JSON.stringify({}),
  })

/** ---------- 定时作业 ---------- */

export const listJobs = (sessionId?: string) =>
  request<{ items: Job[]; timezone: string }>(
    `/api/proactor/jobs${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`
  )

export const addJob = (input: {
  sessionId: string
  mode: 'once' | 'daily' | 'weekly'
  hhmm: string
  brief: string
  weekday?: number
}) =>
  request<{ ok: boolean; item: Job }>('/api/proactor/jobs', {
    method: 'POST',
    body: JSON.stringify({
      session_id: input.sessionId,
      mode: input.mode,
      hhmm: input.hhmm,
      brief: input.brief,
      weekday: input.weekday ?? -1,
    }),
  })

export const toggleJob = (id: number, enabled: boolean) =>
  request<{ ok: boolean }>(`/api/proactor/jobs/${id}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })

export const deleteJob = (id: number) =>
  request<{ ok: boolean }>(`/api/proactor/jobs/${id}`, { method: 'DELETE' })

/** ---------- 自建轮询 ---------- */

export const listPolls = (sessionId?: string) =>
  request<{ items: Poll[] }>(
    `/api/proactor/polls${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`
  )

export const addPoll = (input: {
  sessionId: string
  title: string
  probability?: number
  wakeEveryN?: number
  activeHours?: number
}) =>
  request<{ ok: boolean; item: Poll }>('/api/proactor/polls', {
    method: 'POST',
    body: JSON.stringify({
      session_id: input.sessionId,
      title: input.title,
      probability: input.probability ?? 10,
      wake_every_n: input.wakeEveryN ?? 20,
      active_hours: input.activeHours ?? 12,
    }),
  })

export const togglePoll = (id: number, enabled: boolean) =>
  request<{ ok: boolean }>(`/api/proactor/polls/${id}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })

export const deletePoll = (id: number) =>
  request<{ ok: boolean }>(`/api/proactor/polls/${id}`, { method: 'DELETE' })

/** ---------- 会话主动开关 ---------- */

export const getProactiveState = (sessionId: string) =>
  request<{ state: ProactiveState | null }>(
    `/api/proactor/proactive/state?session_id=${encodeURIComponent(sessionId)}`
  )

export const setProactiveEnabled = (sessionId: string, enabled: boolean) =>
  request<{ ok: boolean; enabled: boolean }>('/api/proactor/proactive/enable', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, enabled }),
  })