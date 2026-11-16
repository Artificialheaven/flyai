/** 活跃 run 发现与探活：刷新续读、跨设备/多标签页 409 恢复的服务端入口。 */
import { request } from '../api/client.js'

export type RunProbe = {
  status: string
  thread_id?: string
  [key: string]: unknown
}

/** 查询某会话当前是否有进行中的 run（可能由其它标签页/设备发起） */
export const discoverActiveRun = async (sessionId: string): Promise<string | null> => {
  const data = await request<{ run_id?: string }>(
    `/api/chat/active-run?session_id=${encodeURIComponent(sessionId)}`,
    { silent401: true }
  )
  return data && typeof data.run_id === 'string' && data.run_id ? data.run_id : null
}

/** 探活：服务重启/运行过期会 404；status !== 'running' 说明已落盘结束 */
export const probeRun = async (runId: string): Promise<RunProbe> =>
  request<RunProbe>(`/api/chat/runs/${encodeURIComponent(runId)}`, { silent401: true })
