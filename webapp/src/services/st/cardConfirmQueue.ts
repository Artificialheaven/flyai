/**
 * 卡片确认弹窗的全局闸门。
 *
 * 同一张卡连续发起多个请求、或页面上多张卡同时请求网络时，只允许一个
 * 确认弹窗出现；其余按到达顺序排队。持有方（卡片组件）拿到 release 后
 * 展示弹窗，确认/取消/卸载时必须调用 release 放行下一个。
 */

export type ConfirmRelease = () => void

export type ConfirmTicket = {
  /** 轮到本请求展示弹窗时兑现；失败表示排队期间已被取消 */
  ready: Promise<ConfirmRelease>
  /** 卸载/不再需要时调用：从队列移除；已拿到展示权时由持有方 release */
  cancel: () => void
}

type Waiter = {
  grant: (release: ConfirmRelease) => void
  reject: () => void
}

let busy = false
const waiters: Waiter[] = []

const releaseNext = (): void => {
  busy = false
  const next = waiters.shift()
  if (!next) {
    return
  }
  busy = true
  next.grant(releaseNext)
}

export const acquireCardConfirm = (): ConfirmTicket => {
  let waiter: Waiter | null = null
  let settled = false
  const ready = new Promise<ConfirmRelease>((resolve, reject) => {
    const grant = (release: ConfirmRelease): void => {
      if (settled) {
        // 已取消却又被放行：立刻归还名额，避免闸门卡死
        release()
        return
      }
      settled = true
      resolve(release)
    }
    waiter = {
      grant,
      reject: () => {
        if (settled) {
          return
        }
        settled = true
        reject(new Error('cancelled'))
      },
    }
    if (!busy) {
      busy = true
      grant(releaseNext)
    } else {
      waiters.push(waiter)
    }
  })
  return {
    ready,
    cancel: () => {
      if (!waiter) {
        return
      }
      const index = waiters.indexOf(waiter)
      if (index >= 0) {
        waiters.splice(index, 1)
        waiter.reject()
      }
    },
  }
}

/** 测试用：清空闸门状态 */
export const __resetCardConfirmQueue = (): void => {
  busy = false
  waiters.length = 0
}
