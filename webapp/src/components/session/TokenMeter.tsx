import { useState } from 'react'
import { useIsMobile } from '../../app/viewport.js'
import { useTranslate } from '../../i18n/index.js'
import type { Session } from '../../services/chat/types.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Tooltip } from '../base/Tooltip.js'
import styles from './TokenMeter.module.css'

const compact = (value: number): string => {
  if (value < 1000) {
    return String(Math.round(value))
  }
  if (value < 1_000_000) {
    const k = value / 1000
    return `${k < 100 ? k.toFixed(1).replace(/\.0$/, '') : Math.round(k)}k`
  }
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}m`
}

/** 会话累计 Token 用量（后端 token_usage 为整段会话累加，不是单轮上下文水位） */
export const TokenMeter = ({ session }: { session: Session }) => {
  const t = useTranslate()
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  const usage = session.tokenUsage
  const prompt = usage?.prompt_tokens ?? 0
  const completion = usage?.completion_tokens ?? 0
  const cached = usage?.cached_tokens ?? 0
  const total = usage?.total_tokens ?? prompt + completion
  if (total <= 0) {
    return null
  }
  // 缓存命中率：输入 token 中命中缓存的比例（cached_tokens 已包含在 prompt_tokens 内）
  const cacheRate = prompt > 0 ? (cached / prompt) * 100 : 0
  const rateText =
    cacheRate > 0 ? cacheRate.toFixed(1).replace(/\.0$/, '') : '0'
  const detail = (
    <div className={styles.detail}>
      <div className={styles.detailTitle}>{t('tokenUsageTooltip')}</div>
      <div>{t('tokenUsagePrompt', { count: prompt.toLocaleString() })}</div>
      <div>{t('tokenUsageCompletion', { count: completion.toLocaleString() })}</div>
      <div>{t('tokenUsageTotal', { count: total.toLocaleString() })}</div>
      <div>
        {t('tokenUsageCache', {
          rate: rateText,
          count: cached.toLocaleString(),
        })}
      </div>
    </div>
  )
  const label = t('tokenUsageLabel', { count: compact(total) })
  if (isMobile) {
    return (
      <>
        <button
          type="button"
          className={`${styles.meter} ${styles.tappable}`}
          aria-label={t('tokenUsageTooltip')}
          onClick={() => setOpen(true)}
        >
          {label}
        </button>
        {open ? (
          <Modal
            title={t('tokenUsageTooltip')}
            closable
            onClose={() => setOpen(false)}
            footer={<Button onClick={() => setOpen(false)}>{t('close')}</Button>}
          >
            {detail}
          </Modal>
        ) : null}
      </>
    )
  }
  return (
    <Tooltip content={detail}>
      <span className={styles.meter}>{label}</span>
    </Tooltip>
  )
}
