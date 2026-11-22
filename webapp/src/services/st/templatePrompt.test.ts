import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./templateRunner.js', () => ({ runTemplateCells: vi.fn() }))
vi.mock('../flyai/templates.js', () => ({ commitTemplatePrompt: vi.fn() }))

import { commitTemplatePrompt } from '../flyai/templates.js'
import { runTemplateCells } from './templateRunner.js'
import { handleTemplatePrepare } from './templatePrompt.js'

const runner = vi.mocked(runTemplateCells)
const commit = vi.mocked(commitTemplatePrompt)

const event = {
  type: 'template_prepare' as const,
  prepareId: 'a'.repeat(24),
  runId: 'run1',
  cells: [{ id: 'p1', text: '<%= 1 + 1 %>' }],
  context: { defaultScope: 'local', escapeOutput: false },
  limits: { totalMs: 8000 },
  contextVersion: 'v1',
  timeoutMs: 10000,
}

beforeEach(() => {
  runner.mockReset()
  commit.mockReset()
  commit.mockResolvedValue({ ok: true })
})

describe('提示词侧 prepare 处理', () => {
  it('执行沙箱并回传单元格与 ops', async () => {
    runner.mockResolvedValue({
      cells: { p1: '2' },
      ops: [{ op: 'vars.set', scope: 'local', path: 'x', value: 1 }],
      errors: [],
      stats: { ms: 3, count: 1 },
    })
    await handleTemplatePrepare(event, null)
    expect(runner).toHaveBeenCalledTimes(1)
    const [cells, context] = runner.mock.calls[0]
    expect(cells).toEqual(event.cells)
    expect(context?.escapeOutput).toBe(false)
    expect(context?.limits?.totalMs).toBe(8000)
    expect(commit).toHaveBeenCalledWith({
      run_id: 'run1',
      prepare_id: event.prepareId,
      context_version: 'v1',
      cells: { p1: '2' },
      ops: [{ op: 'vars.set', scope: 'local', path: 'x', value: 1 }],
      errors: [],
      stats: { ms: 3, count: 1 },
    })
  })

  it('沙箱失败时回传 errors，避免服务端白等超时', async () => {
    runner.mockRejectedValue(new Error('模板执行超时'))
    await handleTemplatePrepare(event, null)
    const payload = commit.mock.calls[0][0]
    expect(payload.cells).toEqual({})
    expect(payload.errors[0].message).toContain('超时')
  })

  it('没有运行 id 时不执行也不提交', async () => {
    await handleTemplatePrepare({ ...event, runId: undefined }, null)
    expect(runner).not.toHaveBeenCalled()
    expect(commit).not.toHaveBeenCalled()
  })
})
