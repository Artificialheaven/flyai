import { request } from './client.js'

/** P4 生态扩展管理（/api/agentdb）：MCP server、watcher、提示词分区，均管理员门禁。 */

export type McpServer = {
  id: number
  server_id: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: number
  status: string
  tools: { name?: string; description?: string }[]
  last_error: string
  created_at: number
  updated_at: number
}

export type Watcher = {
  id: number
  session_id: string
  script_path: string
  source: string
  pid: number
  status: string
  log_path: string
  created_at: number
  stopped_at: number
}

export type PromptPartition = {
  key: string
  desc: string
  placeholders: string[]
  default: string
  override: string
  updated_at: number
}

/** ---------- MCP ---------- */

export const listMcpServers = () =>
  request<{ items: McpServer[] }>('/api/agentdb/mcp/servers')

export const createMcpServer = (input: {
  serverId: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: boolean
}) =>
  request<{ ok: boolean; item: McpServer }>('/api/agentdb/mcp/servers', {
    method: 'POST',
    body: JSON.stringify({
      server_id: input.serverId,
      command: input.command,
      args: input.args,
      env: input.env,
      enabled: input.enabled,
    }),
  })

export const updateMcpServer = (
  serverId: string,
  input: {
    command?: string
    args?: string[]
    env?: Record<string, string>
    enabled?: boolean
  }
) =>
  request<{ ok: boolean; item: McpServer }>(
    `/api/agentdb/mcp/servers/${encodeURIComponent(serverId)}`,
    { method: 'PUT', body: JSON.stringify(input) }
  )

export const setMcpEnabled = (serverId: string, enabled: boolean) =>
  request<{ ok: boolean; item: McpServer }>(
    `/api/agentdb/mcp/servers/${encodeURIComponent(serverId)}/enabled`,
    { method: 'POST', body: JSON.stringify({ enabled }) }
  )

export const testMcpServer = (serverId: string) =>
  request<{ ok: boolean; tools: { name?: string; description?: string }[] }>(
    `/api/agentdb/mcp/servers/${encodeURIComponent(serverId)}/test`,
    { method: 'POST', body: JSON.stringify({}) }
  )

export const deleteMcpServer = (serverId: string) =>
  request<{ ok: boolean }>(`/api/agentdb/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: 'DELETE',
  })

/** ---------- watcher ---------- */

export const listWatchers = (sessionId?: string) =>
  request<{ items: Watcher[] }>(
    `/api/agentdb/watchers${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`
  )

export const stopWatcher = (id: number) =>
  request<{ ok: boolean; item: Watcher }>('/api/agentdb/watchers/stop', {
    method: 'POST',
    body: JSON.stringify({ id }),
  })

export const reconcileWatchers = () =>
  request<{ ok: boolean; stopped: number }>('/api/agentdb/watchers/reconcile', {
    method: 'POST',
    body: JSON.stringify({}),
  })

/** ---------- 提示词分区 ---------- */

export const listPrompts = () =>
  request<{ items: PromptPartition[] }>('/api/agentdb/prompts')

export const setPrompt = (key: string, text: string) =>
  request<{ ok: boolean }>(`/api/agentdb/prompts/${encodeURIComponent(key)}`, {
    method: 'PUT',
    body: JSON.stringify({ text }),
  })

export const resetPrompt = (key: string) =>
  request<{ ok: boolean; reset: boolean }>(`/api/agentdb/prompts/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })