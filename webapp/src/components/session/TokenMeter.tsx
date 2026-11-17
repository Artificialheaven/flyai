import { useTranslate } from '../../i18n/index.js'
import type { Session } from '../../services/chat/types.js'
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
  const usage = session.tokenUsage
  const prompt = usage?.prompt_tokens ?? 0
  const completion = usage?.completion_tokens ?? 0
  const total = usage?.total_tokens ?? prompt + completion
  if (total <= 0) {
    return null
  }
  return (
    <Tooltip
      content={
        <div className={styles.detail}>
          <div className={styles.detailTitle}>{t('tokenUsageTooltip')}</div>
          <div>{t('tokenUsagePrompt', { count: prompt.toLocaleString() })}</div>
          <div>{t('tokenUsageCompletion', { count: completion.toLocaleString() })}</div>
          <div>{t('tokenUsageTotal', { count: total.toLocaleString() })}</div>
        </div>
      }
    >
      <span className={styles.meter}>{t('tokenUsageLabel', { count: compact(total) })}</span>
    </Tooltip>
  )
}