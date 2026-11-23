/**
 * 卡内授权动作的后端接口（L4-B/L4-C）：会话副本写回与受控网络代理。
 *
 * 这些调用只会在用户于确认弹窗中同意后发出；服务端仍会复核用户开关、
 * 活动 run、字段白名单与网络策略。
 */

import { request } from './client.js'
import {
  readCachedNetProxy,
  writeCachedNetProxy,
} from './cardResourceCache.js'
import { runNetProxy } from './netProxyQueue.js'
import type { Character } from './types.js'

export type MvuWriteBody = {
  mode: 'patch' | 'replace'
  stat_data?: Record<string, unknown>
  ops?: Record<string, unknown>[]
  /** 私聊窗口 id；缺省为会话主线程 */
  thread_id?: string
}

export const writeSessionMvu = (
  sessionId: string,
  body: MvuWriteBody
): Promise<{ stat_data: Record<string, unknown> }> =>
  request(`/api/sessions/${encodeURIComponent(sessionId)}/st/mvu`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const writeSessionWorld = (
  sessionId: string,
  entries: unknown[]
): Promise<{ entries?: unknown[]; scoped: boolean }> =>
  request(`/api/sessions/${encodeURIComponent(sessionId)}/st/world`, {
    method: 'POST',
    // compact：大卡条目表可达数 MB，卡内运行时不需要回显
    body: JSON.stringify({ entries, compact: true }),
  })

export const writeSessionCharacter = (
  sessionId: string,
  fields: Record<string, unknown>
): Promise<{ character?: Character; scoped: boolean }> =>
  request(`/api/sessions/${encodeURIComponent(sessionId)}/st/character`, {
    method: 'POST',
    body: JSON.stringify({ fields, compact: true }),
  })

export type NetProxyPayload = {
  method: 'GET' | 'HEAD' | 'POST'
  url: string
  /** 只透传 accept/content-type，其余由服务端丢弃 */
  headers?: Record<string, string>
  /** POST 请求体（标准 base64，不含 data: 前缀） */
  body_base64?: string
}

export type NetProxyResult = {
  status: number
  headers: Record<string, string>
  body_base64: string
  truncated: boolean
}

export const proxyNet = async (
  payload: NetProxyPayload,
  signal?: AbortSignal
): Promise<NetProxyResult> => {
  // 静态资源命中本地 IndexedDB 缓存时完全不走服务器
  const cached = await readCachedNetProxy(payload)
  if (cached) {
    return cached
  }
  // 页面级排队（并发 3）+ 429 退避重试，避免卡片批量抓文件时撞服务端限流
  return runNetProxy(async () => {
    // 排队期间可能已有其它请求写入同一资源，再查一次
    const again = await readCachedNetProxy(payload)
    if (again) {
      return again
    }
    const result = await request<NetProxyResult>('/api/st/net-proxy', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    })
    await writeCachedNetProxy(payload, result)
    return result
  }, signal)
}
