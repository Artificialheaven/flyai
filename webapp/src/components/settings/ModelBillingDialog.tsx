import { useTranslate } from '../../i18n/index.js'
import type { ProviderView } from '../../services/flyai/admin.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import styles from './admin/AdminPanels.module.css'

const fmtNum = (value: number): string =>
  value.toLocaleString(undefined, { maximumFractionDigits: 4 })

const PriceLine = ({ label, value }: { label: string; value: number }) => (
  <div className={styles.billingRow}>
    <span>{label}</span>
    <span className={styles.billingPrice}>{fmtNum(value)}</span>
  </div>
)

/** 模型计费说明：按轮次固定扣费，或按实际 token 计费（含分时段/日期段覆盖价格） */
export const ModelBillingDialog = ({
  provider,
  onClose,
}: {
  provider: ProviderView
  onClose: () => void
}) => {
  const t = useTranslate()
  const pricing = provider.pricing ?? null
  const periods = pricing?.periods ?? []

  return (
    <Modal
      title={t('billingTitle', { model: provider.id })}
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={onClose}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" onClick={onClose}>
            {t('billingClose')}
          </Button>
        </div>
      }
    >
      {!pricing ? (
        <>
          <div className={styles.sectionTitle}>{t('billingRoundTitle')}</div>
          <div className={styles.rows}>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>
                  {t('billingRoundDesc', { rate: fmtNum(provider.rate ?? 1) })}
                </div>
                <div className={styles.rowDesc}>{t('billingRoundHint')}</div>
              </div>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className={styles.sectionTitle}>{t('billingTokenTitle')}</div>
          <div className={styles.hint}>{t('billingTokenDesc')}</div>
          <div className={styles.pricingBox}>
            <PriceLine label={t('billingCacheHit')} value={pricing.cache_hit_input ?? 0} />
            <PriceLine label={t('billingInput')} value={pricing.input ?? 0} />
            <PriceLine label={t('billingOutput')} value={pricing.output ?? 0} />
          </div>
          <div className={styles.hint}>{t('billingPerMillion')}</div>

          <div className={styles.sectionTitle}>{t('billingPeriodsTitle')}</div>
          {periods.length === 0 ? (
            <div className={styles.hint}>{t('billingNoPeriods')}</div>
          ) : (
            periods.map((period, index) => {
              const dateText =
                period.date_start && period.date_end
                  ? `${period.date_start} ~ ${period.date_end}`
                  : t('billingAlways')
              const daily =
                period.daily_start && period.daily_end
                  ? `${period.daily_start}–${period.daily_end}`
                  : ''
              const crossMidnight = Boolean(daily && period.daily_start! > period.daily_end!)
              return (
                <div key={index} className={styles.periodCard}>
                  <div className={styles.periodHead}>
                    <span>{period.name || `#${index + 1}`}</span>
                  </div>
                  <div className={styles.billingPeriodMeta}>
                    <span>{dateText}</span>
                    {daily ? <span>{daily}</span> : null}
                    {crossMidnight ? <span>{t('billingCrossMidnight')}</span> : null}
                  </div>
                  <PriceLine
                    label={t('billingCacheHit')}
                    value={period.cache_hit_input ?? 0}
                  />
                  <PriceLine label={t('billingInput')} value={period.input ?? 0} />
                  <PriceLine label={t('billingOutput')} value={period.output ?? 0} />
                </div>
              )
            })
          )}
        </>
      )}
      <div className={styles.billingNote}>{t('billingNote')}</div>
    </Modal>
  )
}
