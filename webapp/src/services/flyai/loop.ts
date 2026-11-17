import { request } from './client.js'

/** 长跑模式接口（/api/chat/loop）：申请审批、启动、列表、停止与安全点打断。 */

export type LoopRequest = {
  id: number
  session_id: string
  task: string
  reason: string
  requester_uid: string
  status: 'pending' | 'approved' | 'rejected' | string
  decided_by: string
  decided_at: number
  created_at: number
}

export type LoopSession = {
  id: number
  session_id: string
  task: string
  status: string
  request_id: number
  rounds: number
  compacts: number
  compact_note: string
  result: string
  error: string
  created_at: number
  ended_at: number
}

export const startLoop = (input: {
  sessionId: string
  task: string
  providerId?: string
}) =>
  request<{ ok: boolean; run_id: string | null; loop_id: number | null }>(
    '/api/chat/loop/start',
    {
      method: 'POST',
      body: JSON.stringify({
        session_id: input.sessionId,
        task: input.task,
        provider_id: input.providerId,
      }),
    }
  )

export const listLoopRequests = (status?: string) =>
  request<{ items: LoopRequest[] }>(
    `/api/chat/loop/requests${status ? `?status=${encodeURIComponent(status)}` : ''}`
  )

export const decideLoopRequest = (requestId: number, uid: string, approve: boolean) =>
  request<{ ok: boolean; status: string; run_id?: string | null; loop_id?: number | null }>(
    `/api/chat/loop/requests/${requestId}/decide`,
    { method: 'POST', body: JSON.stringify({ uid, approve }) }
  )

export const listLoopSessions = (status?: string) =>
  request<{ items: LoopSession[] }>(
    `/api/chat/loop/sessions${status ? `?status=${encodeURIComponent(status)}` : ''}`
  )

export const stopLoop = (loopId: number) =>
  request<{ ok: boolean; loop_id: number; status: string }>(
    `/api/chat/loop/sessions/${loopId}/stop`,
    { method: 'POST', body: JSON.stringify({}) }
  )

export const interruptLoop = (sessionId: string, text: string) =>
  request<{ ok: boolean; loop_id: number; pending: number; dropped: number }>(
    '/api/chat/loop/interrupt',
    { method: 'POST', body: JSON.stringify({ session_id: sessionId, text }) }
  )

export const loopHubPending = (sessionId: string) =>
  request<{ session_id: string; pending: number }>(
    `/api/chat/loop/hub/pending?session_id=${encodeURIComponent(sessionId)}`
  )