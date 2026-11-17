import { useEffect, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { getMyQuota, type QuotaView } from '../../services/flyai/admin.js'
import { redeemCdk } from '../../services/flyai/cdk.js'
import { showToast } from '../base/toastState.js'
import styles from './Settings.module.css'

const formatPoints = (value: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** 数据管理主入口：明文 CDK 输入 + 兑换按钮，兑换后直接增加额度（点数） */
export const CdkRedeemField = () => {
  const t = useTranslate()
  const [value, setValue] = useState('')
  const [quota, setQuota] = useState<QuotaView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    getMyQuota()
      .then((next) => {
        if (!cancelled) {
          setQuota(next)
        }
      })
      .catch(() => {
        // 余额只是辅助展示，失败不阻塞兑换
      })
    return () => {
      cancelled = true
    }
  }, [])

  const unlimited = Boolean(quota?.unlimited) || quota?.total === -1
  const remaining = quota ? (quota.remaining ?? 0) : 0

  const submit = async () => {
    if (busy) {
      return
    }
    const code = value.trim()
    if (!code) {
      setError(t('cdkRedeemRequired'))
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await redeemCdk(code)
      if (result.quota) {
        setQuota(result.quota)
      }
      setValue('')
      showToast({ key: 'cdkRedeemSuccess', params: { amount: formatPoints(result.amount) } })
    } catch (err) {
      setError(
        t('cdkRedeemFailed', {
          message: err instanceof Error ? err.message : t('unknownError'),
        })
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.cell}>
      <div className={styles.apiKey}>
        <div className={styles.label}>{t('cdkRedeemTitle')}</div>
        <div className={styles.apiKeyInput}>
          <input
            type="text"
            placeholder={t('cdkRedeemPlaceholder')}
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                void submit()
              }
            }}
          />
          <span className={styles.divider} />
          <button
            type="button"
            className={styles.confirm}
            disabled={busy}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => void submit()}
          >
            {t(busy ? 'cdkRedeeming' : 'cdkRedeemAction')}
          </button>
        </div>
        {quota ? (
          <div className={styles.description}>
            {unlimited
              ? t('cdkBalanceUnlimited')
              : t('cdkBalance', { amount: formatPoints(remaining) })}
          </div>
        ) : null}
        {error ? <div className={styles.error}>{error}</div> : null}
      </div>
    </div>
  )
}