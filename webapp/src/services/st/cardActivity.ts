/**
 * 卡片写回活动（全局）：变量/世界书/角色字段同步进会话时，在会话头部
 * 下沿显示进度条。写回是单个请求，没有真实百分比；这里按耗时推进到 95%，
 * 结束后补到 100% 再淡出，作为非阻塞反馈。
 */

import { create } from 'zustand'

type CardActivityState = {
  /** 进行中的写回数量（卡片可能并发多次） */
  active: number
  /** 本轮第一个写回的开始时间；0 表示空闲 */
  startedAt: number
  label: string
}

export const useCardActivity = create<CardActivityState>(() => ({
  active: 0,
  startedAt: 0,
  label: '',
}))

export const beginCardActivity = (label: string): void => {
  useCardActivity.setState((state) => ({
    active: state.active + 1,
    startedAt: state.active === 0 ? Date.now() : state.startedAt,
    label: label || state.label,
  }))
}

export const endCardActivity = (): void => {
  useCardActivity.setState((state) => {
    const active = Math.max(0, state.active - 1)
    return active === 0 ? { active, startedAt: 0 } : { active }
  })
}
