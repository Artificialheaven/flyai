import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import {
  claimAfdianOrder,
  fetchAfdianInfo,
  fetchMyAfdianOrders,
  type AfdianPublicInfo,
  type MyAfdianOrder,
} from '../../services/flyai/afdian.js'
import { redeemCdk } from '../../services/flyai/cdk.js'
import { Button } from '../base/Button.js'
import { showToast } from '../base/toastState.js'
import styles from './Settings.module.css'

const formatPoints = (value: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * 数据管理页的爱发电入口：
 * 购买按钮（可跳转店铺）+ 订单号领取兜底 + 已绑定订单列表（卡密一键兑换）。
 * 爱发电未启用或接口不可用时整卡隐藏，不影响数据面板其它功能。
 */
export const AfdianClaimCard = () => {
  const t = useTranslate()
  const [info, setInfo] = useState<AfdianPublicInfo | null>(null)
  const [orders, setOrders] = useState<MyAfdianOrder[]>([])
  const [value, setValue] = useState('')
  const [claiming, setClaiming] = useState(false)
  const [redeeming, setRedeeming] = useState('')
  const [error, setError] = useState('')

  const loadOrders = useCallback(async () => {
    try {
      setOrders(await fetchMyAfdianOrders())
    } catch {
      // 根单只影响领取列表，失败保持当前展示
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchAfdianInfo()
      .then((next) => {
        if (!cancelled) {
          setInfo(next)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setInfo(null)
        }
      })
    void loadOrders()
    return () => {
      cancelled = true
    }
  }, [loadOrders])

  const claim = async () => {
    if (claiming) {
      return
    }
    const orderNo = value.trim()
    if (!orderNo) {
      setError(t('afdianClaimRequired'))
      return
    }
    setClaiming(true)
    setError('')
    try {
      await claimAfdianOrder(orderNo)
      setValue('')
      showToast({ key: 'afdianClaimSuccess' })
      await loadOrders()
    } catch (err) {
      setError(
        t('afdianClaimFailed', {
          message: err instanceof Error ? err.message : t('unknownError'),
        })
      )
    } finally {
      setClaiming(false)
    }
  }

  const redeem = async (order: MyAfdianOrder) => {
    if (redeeming) {
      return
    }
    setRedeeming(order.out_trade_no)
    setError('')
    try {
      const result = await redeemCdk(order.code)
      showToast({ key: 'cdkRedeemSuccess', params: { amount: formatPoints(result.amount) } })
      await loadOrders()
    } catch (err) {
      setError(
        t('cdkRedeemFailed', {
          message: err instanceof Error ? err.message : t('unknownError'),
        })
      )
    } finally {
      setRedeeming('')
    }
  }

  if (!info?.enabled) {
    return null
  }
  if (!info.purchase_url && orders.length === 0) {
    return null
  }

  return (
    <div className={styles.cell}>
      <div className={styles.apiKey}>
        {info.purchase_url ? (
          <div className={styles.row}>
            <div className={styles.text}>
              <div className={styles.label}>{t('afdianPurchaseEntryTitle')}</div>
              <div className={styles.description}>{t('afdianPurchaseEntryDesc')}</div>
            </div>
            <Button
              size="m"
              onClick={() =>
                window.open(info.purchase_url, '_blank', 'noopener,noreferrer')
              }
            >
              {info.purchase_label || t('afdianBuyFallback')}
            </Button>
          </div>
        ) : null}
        <div className={styles.label}>{t('afdianClaimTitle')}</div>
        <div className={styles.description}>{t('afdianClaimDesc')}</div>
        <div className={styles.apiKeyInput}>
          <input
            type="text"
            placeholder={t('afdianClaimPlaceholder')}
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                void claim()
              }
            }}
          />
          <span className={styles.divider} />
          <button
            type="button"
            className={styles.confirm}
            disabled={claiming}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => void claim()}
          >
            {t(claiming ? 'afdianClaiming' : 'afdianClaimButton')}
          </button>
        </div>
        {orders.length > 0 ? (
          <div className={styles.afdianOrders}>
            <div className={styles.description}>{t('afdianMyOrders')}</div>
            {orders.map((order) => (
              <div key={order.out_trade_no} className={styles.afdianOrder}>
                <div className={styles.afdianOrderMain}>
                  <div className={styles.label}>
                    {order.plan_title || order.out_trade_no}
                  </div>
                  <div className={styles.afdianOrderMeta}>
                    {t('afdianOrderMeta', {
                      amount: formatPoints(order.amount),
                      credits: formatPoints(order.credits),
                      date: order.issued_at
                        ? new Date(order.issued_at * 1000).toLocaleDateString()
                        : '',
                    })}
                  </div>
                  <div className={styles.afdianCode}>{order.code}</div>
                </div>
                <Button
                  size="s"
                  variant="outlined"
                  disabled={order.redeemed || Boolean(redeeming)}
                  onClick={() => void redeem(order)}
                >
                  {t(order.redeemed ? 'afdianRedeemed' : 'afdianRedeem')}
                </Button>
              </div>
            ))}
          </div>
        ) : null}
        {error ? <div className={styles.error}>{error}</div> : null}
      </div>
    </div>
  )
}
