import { commitTemplatePrompt } from '../flyai/templates.js'
import type { ChatStreamEvent } from '../flyai/events.js'
import {
  runTemplateCells,
  type TemplateContext,
} from './templateRunner.js'

/** 提示词侧 EJS：收到 prepare 后在沙箱执行，并把单元格与 ops 交回服务端。 */

export type TemplatePrepareEvent = Extract<ChatStreamEvent, { type: 'template_prepare' }>

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error)

/** 失败也要回传（带 errors），避免服务端白等 10 秒超时才中止。 */
export const handleTemplatePrepare = async (
  event: TemplatePrepareEvent,
  fallbackRunId: string | null
): Promise<void> => {
  const runId = event.runId || fallbackRunId || ''
  if (!runId) {
    return
  }
  const base = {
    run_id: runId,
    prepare_id: event.prepareId,
    context_version: event.contextVersion,
  }
  try {
    const limits = event.limits || {}
    const totalMs = Math.max(
      500,
      Math.min(Number(limits.totalMs) || 8000, 15000)
    )
    const context = {
      ...(event.context as TemplateContext),
      escapeOutput: false,
      limits: { ...limits, totalMs },
    }
    const result = await runTemplateCells(event.cells, context)
    await commitTemplatePrompt({
      ...base,
      cells: result.cells,
      ops: result.ops,
      errors: result.errors,
      stats: result.stats,
    })
  } catch (error) {
    await commitTemplatePrompt({
      ...base,
      cells: {},
      ops: [],
      errors: [{ message: errorText(error) || '模板渲染失败' }],
      stats: { ms: 0, count: 0 },
    }).catch(() => {
      // 服务端已超时/运行已结束时提交失败是正常竞态，静默忽略
    })
  }
}
