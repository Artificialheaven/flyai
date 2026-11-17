import { request } from './client.js'

/** 记忆管理接口（/api/my/memory，字段与 routes_memory.py 原样对齐）。 */

export type MemoryEntry = {
  id: string
  text: string
  source: string
  time_anchor: string
  entities: string[]
  tags: string[]
  importance: number
  created_at: string
  last_accessed: string
  access_count: number
  superseded: boolean
  history: { text?: string; at?: string; [key: string]: unknown }[]
}

export type MemoryEntryWithChar = MemoryEntry & { char_id: string }

export type MemoryStats = {
  per_char: {
    char_id: string
    entries: number
    active: number
    superseded: number
    ledger: number
  }[]
  total: { entries: number; active: number; superseded: number; ledger: number }
}

export type MemorySource = {
  char_id: string
  char_name: string
  session_id: string
  session_title: string
  active: number
  superseded: number
  ledger: number
  orphan: boolean
}

export type LedgerItem = {
  id: string
  kind: string
  name: string
  state: string
  first_seen: string
  updated_at: string
  status: string
}

export type LedgerGroup = { kind: string; items: LedgerItem[] }

export type RecallLog = {
  ts: number
  mode?: string
  session_id?: string
  char_id?: string
  query?: string
  items?: { text?: string; score?: number; source?: string }[]
  count?: number
  [key: string]: unknown
}

export const listMemory = (params: {
  charId?: string
  q?: string
  includeSuperseded?: boolean
  limit?: number
}) => {
  const query = new URLSearchParams()
  if (params.charId) {
    query.set('char_id', params.charId)
  }
  if (params.q) {
    query.set('q', params.q)
  }
  if (params.includeSuperseded) {
    query.set('include_superseded', 'true')
  }
  query.set('limit', String(params.limit ?? 200))
  return request<{ entries: MemoryEntryWithChar[]; total: number }>(
    `/api/my/memory?${query.toString()}`
  )
}

export const memoryStats = () => request<MemoryStats>('/api/my/memory/stats')

export const memorySources = () =>
  request<{ sources: MemorySource[] }>('/api/my/memory/sources')

export const memoryLedger = (charId: string) =>
  request<{ char_id: string; groups: LedgerGroup[] }>(
    `/api/my/memory/ledger?char_id=${encodeURIComponent(charId)}`
  )

export const recallLogs = (limit = 100) =>
  request<{ logs: RecallLog[] }>(`/api/my/memory/recall-logs?limit=${limit}`)

export const deleteMemory = (entryId: string, charId?: string) => {
  const query = charId ? `?char_id=${encodeURIComponent(charId)}` : ''
  return request<{ ok: boolean; removed: number }>(
    `/api/my/memory/${encodeURIComponent(entryId)}${query}`,
    { method: 'DELETE' }
  )
}