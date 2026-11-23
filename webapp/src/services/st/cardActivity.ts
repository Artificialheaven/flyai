/**
 * 卡片写回活动（全局）：变量/世界书/角色字段同步进会话时，在会话头部
 * 下沿显示进度条。
 *
 * 世界书写入通过 XHR 的 upload 事件上报真实字节进度（大卡条目表可达数 MB），
 * 失败自动重试时把尝试次数写进 label；没有字节进度的写回仍按时长兜底推进。
 */

import { create } from 'zustand'

type CardActivityState = {
  /** 进行中的写回数量（卡片可能并发多次） */
  active: number
  /** 本轮第一个写回的开始时间；0 表示空闲 */
  startedAt: number
  label: string
  /** 真实进度 0..100；0 表示当前写回没有字节进度，由界面按时长推进 */
  percent: number
  /** 失败重试次数（0=首次尝试） */
  attempt: number
}

export const useCardActivity = create<CardActivityState>(() => ({
  active: 0,
  startedAt: 0,
  label: '',
  percent: 0,
  attempt: 0,
}))

export const beginCardActivity = (label: string): void => {
  useCardActivity.setState((state) => ({
    active: state.active + 1,
    startedAt: state.active === 0 ? Date.now() : state.startedAt,
    label: label || state.label,
    percent: 0,
    attempt: 0,
  }))
}

/** 真实进度（上传/处理字节数），0..100 */
export const setCardActivityProgress = (percent: number): void => {
  const value = Math.max(0, Math.min(100, Number(percent) || 0))
  useCardActivity.setState((state) => (state.active > 0 ? { percent: value } : {}))
}

/** 自动重试时更新提示与尝试序号 */
export const setCardActivityAttempt = (attempt: number): void => {
  useCardActivity.setState((state) => (state.active > 0 ? { attempt } : {}))
}

export const endCardActivity = (): void => {
  useCardActivity.setState((state) => {
    const active = Math.max(0, state.active - 1)
    return active === 0
      ? { active, startedAt: 0, percent: 0, attempt: 0 }
      : { active }
  })
}
