import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetNetProxyQueue,
  retryOnRateLimit,
  runNetProxy,
} from './netProxyQueue.js'

beforeEach(() => {
  __resetNetProxyQueue()
})

const flush = async (): Promise<void> => {
  await Promise.resolve()
  await Promise.resolve()
}

describe('卡内网络代理节流', () => {
  it('页面级并发不超过 3，释放后依次启动', async () => {
    const resolvers: Array<(value: number) => void> = []
    const task = () =>
      new Promise<number>((resolve) => {
        resolvers.push(resolve)
      })

    const pending = [runNetProxy(task), runNetProxy(task), runNetProxy(task), runNetProxy(task)]
    await flush()
    expect(resolvers).toHaveLength(3)

    resolvers[0](1)
    await flush()
    expect(resolvers).toHaveLength(4)

    resolvers[1](2)
    resolvers[2](3)
    resolvers[3](4)
    await expect(Promise.all(pending)).resolves.toEqual([1, 2, 3, 4])
  })

  it('排队期间取消不会发出请求', async () => {
    const resolvers: Array<(value: number) => void> = []
    const block = () =>
      new Promise<number>((resolve) => {
        resolvers.push(resolve)
      })
    const blockers = [runNetProxy(block), runNetProxy(block), runNetProxy(block)]
    const controller = new AbortController()
    const cancelledTask = vi.fn(() => Promise.resolve(2))
    const cancelled = runNetProxy(cancelledTask, controller.signal)

    controller.abort()
    await expect(cancelled).rejects.toMatchObject({ name: 'AbortError' })
    expect(cancelledTask).not.toHaveBeenCalled()

    resolvers.forEach((resolve) => resolve(0))
    await Promise.all(blockers)
  })

  it('429 按 Retry-After 退避后重试', async () => {
    const task = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce({ status: 429, retryAfter: 0.001 })
      .mockRejectedValueOnce({ status: 429, retryAfter: 0.001 })
      .mockResolvedValueOnce('ok')

    await expect(retryOnRateLimit(task)).resolves.toBe('ok')
    expect(task).toHaveBeenCalledTimes(3)
  })

  it('非 429 不重试', async () => {
    const error = { status: 500 }
    const task = vi.fn(() => Promise.reject(error))
    await expect(retryOnRateLimit(task)).rejects.toBe(error)
    expect(task).toHaveBeenCalledTimes(1)
  })
})
