/**
 * 卡片写回进度条：挂在会话头部下沿，非阻塞（pointer-events: none）。
 * 单请求没有真实百分比，按耗时推进；结束后显示 100% 再收起。
 */

import { useEffect, useRef, useState } from 'react'
import { useCardActivity } from '../../services/st/cardActivity.js'
import styles from './CardSyncBar.module.css'

const CREEP_MS = 6000

export const CardSyncBar = () => {
  const { active, startedAt, label, percent: realPercent, attempt } = useCardActivity()
  const [percent, setPercent] = useState(0)
  const [visible, setVisible] = useState(false)
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (active > 0 && startedAt > 0) {
      if (doneTimer.current) {
        clearTimeout(doneTimer.current)
        doneTimer.current = null
      }
      setVisible(true)
      // 有真实上传进度时直接采用；否则按时长推进到 95% 等待完成
      if (realPercent > 0) {
        setPercent(realPercent)
        return undefined
      }
      const tick = () => {
        const elapsed = Date.now() - startedAt
        setPercent(Math.min(95, (elapsed / CREEP_MS) * 95))
      }
      tick()
      const timer = setInterval(tick, 160)
      return () => clearInterval(timer)
    }
    if (!visible) {
      return
    }
    setPercent(100)
    doneTimer.current = setTimeout(() => {
      doneTimer.current = null
      setVisible(false)
      setPercent(0)
    }, 450)
    return undefined
    // visible 只作为“正在显示”的状态位，不参与重启判断
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, startedAt, realPercent])

  useEffect(
    () => () => {
      if (doneTimer.current) {
        clearTimeout(doneTimer.current)
      }
    },
    []
  )

  if (!visible) {
    return null
  }

  return (
    <div className={styles.wrap} aria-hidden={!visible}>
      <div
        className={styles.track}
        role="progressbar"
        aria-busy={active > 0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
        aria-label={label || undefined}
      >
        <span className={styles.fill} style={{ width: `${percent}%` }} />
      </div>
      {label ? (
        <span className={styles.label}>
          {label}
          {attempt > 0 ? `（重试 ${attempt}/3）` : ''} {Math.round(percent)}%
        </span>
      ) : null}
    </div>
  )
}
