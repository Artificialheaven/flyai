import type { TemplateOp } from '../st/templateRunner.js'
import { request } from './client.js'

/** EJS 模板（方案 B）：global 变量与输出侧渲染结果提交。 */

export type TemplateVarsResponse = {
  enabled: boolean
  prompt_enabled?: boolean
  output_enabled?: boolean
  vars?: Record<string, unknown>
}

export const loadTemplateGlobalVars = (): Promise<TemplateVarsResponse> =>
  request<TemplateVarsResponse>('/api/template/vars')

export type TemplateOutputPayload = {
  content: string
  ops: TemplateOp[]
  errors?: Array<{ cellId?: string; message: string }>
  raw_hash: string
  render_id: string
}

export type TemplateOutputResponse = {
  ok: boolean
  idempotent?: boolean
  message?: unknown
  template_vars?: Record<string, unknown>
}

export const commitTemplateOutput = (
  sessionId: string,
  messageId: string,
  payload: TemplateOutputPayload
): Promise<TemplateOutputResponse> =>
  request<TemplateOutputResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(
      messageId
    )}/template-output`,
    { method: 'POST', body: JSON.stringify(payload) }
  )

export type TemplatePromptCommitPayload = {
  run_id: string
  prepare_id: string
  context_version: string
  cells: Record<string, string>
  ops: TemplateOp[]
  errors: Array<{ cellId?: string; message: string }>
  stats?: { ms?: number; count?: number }
}

/** 提示词侧：把沙箱渲染出的单元格与变量 ops 交回等待中的运行。 */
export const commitTemplatePrompt = (
  payload: TemplatePromptCommitPayload
): Promise<{ ok: boolean; accepted?: number }> =>
  request<{ ok: boolean; accepted?: number }>('/api/template/prompt-commit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
