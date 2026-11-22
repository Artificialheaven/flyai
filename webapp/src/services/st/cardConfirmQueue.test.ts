import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetCardConfirmQueue,
  acquireCardConfirm,
} from './cardConfirmQueue.js'

beforeEach(() => {
  __resetCardConfirmQueue()
})

describe('卡片确认弹窗全局闸门', () => {
  it('同一时刻只放行一个，释放后依次放行', async () => {
    const first = acquireCardConfirm()
    const firstRelease = await first.ready

    const second = acquireCardConfirm()
    let secondGranted = false
    second.ready.then(() => {
      secondGranted = true
    })
    await Promise.resolve()
    expect(secondGranted).toBe(false)

    firstRelease()
    const secondRelease = await second.ready
    expect(secondGranted).toBe(true)

    const third = acquireCardConfirm()
    let thirdGranted = false
    third.ready.then(() => {
      thirdGranted = true
    })
    await Promise.resolve()
    expect(thirdGranted).toBe(false)
    secondRelease()
    await Promise.resolve()
    await Promise.resolve()
    expect(thirdGranted).toBe(true)
  })

  it('排队中被取消不会占位，后续请求正常放行', async () => {
    const first = acquireCardConfirm()
    const firstRelease = await first.ready

    const cancelled = acquireCardConfirm()
    const onReject = vi.fn()
    cancelled.ready.catch(onReject)
    cancelled.cancel()
    await Promise.resolve()
    expect(onReject).toHaveBeenCalled()

    const next = acquireCardConfirm()
    let nextGranted = false
    next.ready.then(() => {
      nextGranted = true
    })
    await Promise.resolve()
    expect(nextGranted).toBe(false)

    firstRelease()
    await Promise.resolve()
    await Promise.resolve()
    expect(nextGranted).toBe(true)
  })
})
