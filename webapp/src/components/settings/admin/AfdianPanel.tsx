import { useCallback, useEffect, useState } from 'react'
import { useTranslate, type TranslationKey } from '../../../i18n/index.js'
import { getConfig, putConfig, type AfdianSettingsView } from '../../../services/flyai/admin.js'
import {
  fetchAdminAfdian,
  reissueAfdianOrder,
  verifyAfdianOrder,
  type AfdianOrderRecord,
  type AfdianSummary,
} from '../../../services/flyai/afdian.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { copyText } from '../../../utils/clipboard.js'
import { Button } from '../../base/Button.js'
import { showToast } from '../../base/toastState.js'
import {
  errorText,
  NumberField,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TextField,
} from './AdminCommon.js'
import styles from './AdminPanels.module.css'

/** 爱发电自动发卡（仅管理员）：webhook 触发、query-order 校验后生成绑定 CDK */

type PlanDraft = {
  plan_id: string
  name: string
  amount: string
  ratio: string
}

type FormState = {
  enabled: boolean
  userId: string
  token: string
  apiBase: string
  /** 后端下发明文，仅用于拼回调地址展示，不支持在此编辑 */
  webhookToken: string
  publicBase: string
  maxTotal: string
  defaultAmount: string
  defaultRatio: string
  purchaseUrl: string
  purchaseLabel: string
  plans: PlanDraft[]
}

const toForm = (af?: AfdianSettingsView): FormState => ({
  enabled: af?.enabled ?? false,
  userId: af?.user_id ?? '',
  token: af?.token ?? '',
  apiBase: af?.api_base || 'https://afdian.com',
  webhookToken: af?.webhook_token ?? '',
  publicBase: af?.public_base_url ?? '',
  maxTotal: String(af?.max_total_amount ?? -1),
  defaultAmount: String(af?.default_amount ?? 0),
  defaultRatio: String(af?.default_ratio ?? 1),
  purchaseUrl: af?.purchase_url ?? '',
  purchaseLabel: af?.purchase_label || '去爱发电支持',
  plans: (af?.plans ?? []).map((plan) => ({
    plan_id: plan.plan_id,
    name: plan.name,
    amount: String(plan.amount),
    ratio: String(plan.ratio),
  })),
})

/** 空串按 fallback 处理；非法或小于 min 返回 null */
const parseNum = (text: string, min: number, fallback: number): number | null => {
  const trimmed = text.trim()
  if (!trimmed) {
    return fallback
  }
  const value = Number(trimmed)
  return Number.isFinite(value) && value >= min ? value : null
}

/** 上限：负数一律按 -1（不限）保存 */
const parseCap = (text: string): number | null => {
  const trimmed = text.trim()
  if (!trimmed) {
    return -1
  }
  const value = Number(trimmed)
  if (!Number.isFinite(value)) {
    return null
  }
  return value < 0 ? -1 : value
}

const STATE_LABELS: Record<string, TranslationKey> = {
  received: 'afdianStateReceived',
  issued: 'afdianStateIssued',
  pending_cap: 'afdianStatePendingCap',
  verify_failed: 'afdianStateVerifyFailed',
  invalid: 'afdianStateInvalid',
}

const fmtNum = (value: number): string =>
  value.toLocaleString(undefined, { maximumFractionDigits: 2 })

const fmtTime = (seconds: number): string =>
  seconds ? new Date(seconds * 1000).toLocaleString() : ''

export const AfdianPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [summary, setSummary] = useState<AfdianSummary | null>(null)
  const [orders, setOrders] = useState<AfdianOrderRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [busyNo, setBusyNo] = useState('')
  const [error, setError] = useState('')

  const loadOrders = useCallback(async () => {
    try {
      const data = await fetchAdminAfdian()
      setSummary(data.summary)
      setOrders(data.orders ?? [])
    } catch {
      // 订单区独立加载：失败不影响配置表单
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setForm(toForm(config.afdian))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
    await loadOrders()
  }, [t, loadOrders])

  useEffect(() => {
    void load()
  }, [load])

  const patch = (next: Partial<FormState>) =>
    setForm((current) => (current ? { ...current, ...next } : current))

  const updatePlan = (index: number, next: Partial<PlanDraft>) =>
    setForm((current) =>
      current
        ? {
            ...current,
            plans: current.plans.map((plan, i) => (i === index ? { ...plan, ...next } : plan)),
          }
        : current
    )

  const addPlan = () =>
    setForm((current) =>
      current
        ? { ...current, plans: [...current.plans, { plan_id: '', name: '', amount: '0', ratio: '1' }] }
        : current
    )

  const removePlan = (index: number) =>
    setForm((current) =>
      current ? { ...current, plans: current.plans.filter((_, i) => i !== index) } : current
    )

  const save = async () => {
    if (!form || saving) {
      return
    }
    const cap = parseCap(form.maxTotal)
    const defaultAmount = parseNum(form.defaultAmount, 0, 0)
    const defaultRatio = parseNum(form.defaultRatio, 0, 1)
    if (cap === null || defaultAmount === null || defaultRatio === null) {
      setError(t('afdianNumbersInvalid'))
      return
    }
    const plans: { plan_id: string; name: string; amount: number; ratio: number }[] = []
    for (const draft of form.plans) {
      const planId = draft.plan_id.trim()
      const amount = parseNum(draft.amount, 0, 0)
      const ratio = parseNum(draft.ratio, 0, 1)
      if (!planId) {
        setError(t('afdianPlanIdRequired'))
        return
      }
      if (amount === null || ratio === null) {
        setError(t('afdianNumbersInvalid'))
        return
      }
      plans.push({ plan_id: planId, name: draft.name.trim(), amount, ratio })
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({
        afdian: {
          enabled: form.enabled,
          user_id: form.userId.trim(),
          token: form.token,
          api_base: form.apiBase.trim() || 'https://afdian.com',
          webhook_token: form.webhookToken,
          public_base_url: form.publicBase.trim(),
          max_total_amount: cap,
          default_amount: defaultAmount,
          default_ratio: defaultRatio,
          plans,
          purchase_url: form.purchaseUrl.trim(),
          purchase_label: form.purchaseLabel.trim() || '去爱发电支持',
        },
      })
      setForm(toForm(saved.afdian))
      showToast({ key: 'adminSaved' })
      await loadOrders()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setSaving(false)
    }
  }

  const runOrder = async (record: AfdianOrderRecord, action: 'verify' | 'reissue') => {
    if (busyNo) {
      return
    }
    setBusyNo(record.out_trade_no)
    setError('')
    try {
      if (action === 'reissue') {
        await reissueAfdianOrder(record.out_trade_no)
      } else {
        await verifyAfdianOrder(record.out_trade_no)
      }
      showToast({ key: 'afdianActionDone' })
      await loadOrders()
    } catch (err) {
      setError(t('afdianActionFailed', { message: errorText(err) }))
    } finally {
      setBusyNo('')
    }
  }

  const webhookUrl = (): string => {
    if (!form?.webhookToken) {
      return ''
    }
    const base =
      form.publicBase.trim().replace(/\/+$/, '') ||
      (typeof window !== 'undefined' ? window.location.origin : '')
    return `${base}/api/app/afdian/webhook?token=${encodeURIComponent(form.webhookToken)}`
  }

  const copyWebhook = async () => {
    const url = webhookUrl()
    if (!url) {
      setError(t('afdianWebhookUnsaved'))
      return
    }
    const ok = await copyText(url)
    showToast(t(ok ? 'afdianCopied' : 'afdianCopyFailed'))
  }

  if (!isAdmin) {
    return (
      <div className={styles.page}>
        <PanelEmpty text={t('systemNotAdmin')} />
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideAfdianOptionLabel')}</div>
          <div className={styles.subtitle}>{t('afdianSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button size="s" disabled={!form || loading || saving} onClick={() => void save()}>
            {t(saving ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}
      {loading || !form ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : (
        <>
          <div className={styles.rows}>
            <SwitchRow
              label={t('afdianEnabled')}
              desc={t('afdianEnabledDesc')}
              checked={form.enabled}
              onChange={(checked) => patch({ enabled: checked })}
            />
            <TextField
              label={t('afdianUserId')}
              hint={t('afdianUserIdHint')}
              value={form.userId}
              placeholder="xxxxxxxx"
              onChange={(value) => patch({ userId: value })}
            />
            <TextField
              label={t('afdianToken')}
              hint={t('afdianTokenSetHint')}
              value={form.token}
              placeholder={t('afdianTokenPlaceholder')}
              type="password"
              onChange={(value) => patch({ token: value })}
            />
            <TextField
              label={t('afdianApiBase')}
              value={form.apiBase}
              placeholder="https://afdian.com"
              onChange={(value) => patch({ apiBase: value })}
            />
            <TextField
              label={t('afdianPublicBase')}
              hint={t('afdianPublicBaseHint')}
              value={form.publicBase}
              placeholder={typeof window !== 'undefined' ? window.location.origin : ''}
              onChange={(value) => patch({ publicBase: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('afdianWebhookUrl')}</div>
          <div className={styles.hint}>{t('afdianWebhookHint')}</div>
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.code}>{webhookUrl() || t('afdianWebhookUnsaved')}</div>
            </div>
            <Button
              variant="outlined"
              size="s"
              disabled={!form.webhookToken}
              onClick={() => void copyWebhook()}
            >
              {t('afdianCopy')}
            </Button>
          </div>

          <div className={styles.sectionTitle}>{t('afdianLimitTitle')}</div>
          <div className={styles.rows}>
            <NumberField
              label={t('afdianMaxAmount')}
              hint={t('afdianMaxAmountHint')}
              value={form.maxTotal}
              placeholder="-1"
              onChange={(value) => patch({ maxTotal: value })}
            />
            <div className={styles.inlineFields}>
              <NumberField
                label={t('afdianDefaultAmount')}
                hint={t('afdianDefaultsHint')}
                value={form.defaultAmount}
                placeholder="0"
                onChange={(value) => patch({ defaultAmount: value })}
              />
              <NumberField
                label={t('afdianDefaultRatio')}
                hint={t('afdianDefaultsHint')}
                value={form.defaultRatio}
                placeholder="1"
                onChange={(value) => patch({ defaultRatio: value })}
              />
            </div>
          </div>

          <div className={styles.sectionTitle}>{t('afdianPurchaseTitle')}</div>
          <div className={styles.rows}>
            <TextField
              label={t('afdianPurchaseUrl')}
              hint={t('afdianPurchaseUrlHint')}
              value={form.purchaseUrl}
              placeholder="https://afdian.com/a/xxxx"
              onChange={(value) => patch({ purchaseUrl: value })}
            />
            <TextField
              label={t('afdianPurchaseLabel')}
              value={form.purchaseLabel}
              placeholder="去爱发电支持"
              onChange={(value) => patch({ purchaseLabel: value })}
            />
          </div>

          <div className={styles.periodsHead}>
            <div>
              <div className={styles.sectionTitle}>{t('afdianPlans')}</div>
              <div className={styles.hint}>{t('afdianPlansHint')}</div>
            </div>
            <Button variant="outlined" size="s" onClick={addPlan}>
              {t('afdianPlanAdd')}
            </Button>
          </div>
          {form.plans.length === 0 ? (
            <div className={styles.hint}>{t('afdianPlansEmpty')}</div>
          ) : (
            form.plans.map((plan, index) => (
              <div key={index} className={styles.periodCard}>
                <div className={styles.periodHead}>
                  <input
                    className={styles.input}
                    value={plan.plan_id}
                    placeholder={t('afdianPlanId')}
                    onChange={(event) => updatePlan(index, { plan_id: event.target.value })}
                  />
                  <input
                    className={styles.input}
                    value={plan.name}
                    placeholder={t('afdianPlanName')}
                    onChange={(event) => updatePlan(index, { name: event.target.value })}
                  />
                  <Button
                    variant="dangerOutlined"
                    size="s"
                    onClick={() => removePlan(index)}
                  >
                    {t('afdianPlanRemove')}
                  </Button>
                </div>
                <div className={styles.inlineFields}>
                  <NumberField
                    label={t('afdianPlanAmount')}
                    hint={t('afdianPlanAmountHint')}
                    value={plan.amount}
                    placeholder="0"
                    onChange={(value) => updatePlan(index, { amount: value })}
                  />
                  <NumberField
                    label={t('afdianPlanRatio')}
                    value={plan.ratio}
                    placeholder="1"
                    onChange={(value) => updatePlan(index, { ratio: value })}
                  />
                </div>
              </div>
            ))
          )}

          <div className={styles.sectionTitle}>{t('afdianOrders')}</div>
          {summary ? (
            <div className={styles.stats}>
              <div className={styles.stat}>
                <div className={styles.statValue}>{fmtNum(summary.cumulative)}</div>
                <div className={styles.statLabel}>{t('afdianCumulative')}</div>
              </div>
              <div className={styles.stat}>
                <div className={styles.statValue}>
                  {summary.cap < 0 ? t('afdianUnlimited') : fmtNum(summary.cap)}
                </div>
                <div className={styles.statLabel}>{t('afdianCap')}</div>
              </div>
              <div className={styles.stat}>
                <div className={styles.statValue}>{summary.pending}</div>
                <div className={styles.statLabel}>{t('afdianPendingCount')}</div>
              </div>
            </div>
          ) : null}
          {orders.length === 0 ? (
            <PanelEmpty text={t('afdianOrdersEmpty')} />
          ) : (
            <div className={styles.list}>
              {orders.map((record) => (
                <div key={record.out_trade_no} className={styles.item}>
                  <div className={styles.itemMain}>
                    <div className={styles.itemName}>
                      <span className={styles.code}>{record.out_trade_no}</span>
                      <span className={styles.badge}>
                        {t(STATE_LABELS[record.state] ?? 'afdianStateInvalid')}
                      </span>
                      {record.bound_uid ? (
                        <span className={styles.badge}>{record.bound_uid}</span>
                      ) : null}
                    </div>
                    <div className={styles.itemMeta}>
                      {[
                        record.plan_title || record.plan_id,
                        `${fmtNum(record.total_amount)} 元`,
                        `${fmtNum(record.credits)} 点`,
                        fmtTime(record.created_at),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                    {record.cdk_code ? (
                      <div className={`${styles.itemMeta} ${styles.code}`}>{record.cdk_code}</div>
                    ) : null}
                    {record.reason ? <div className={styles.error}>{record.reason}</div> : null}
                  </div>
                  <div className={styles.itemActions}>
                    {record.state === 'pending_cap' ? (
                      <Button
                        size="s"
                        variant="outlined"
                        disabled={Boolean(busyNo)}
                        onClick={() => void runOrder(record, 'reissue')}
                      >
                        {t('afdianReissue')}
                      </Button>
                    ) : record.state !== 'issued' ? (
                      <Button
                        size="s"
                        variant="outlined"
                        disabled={Boolean(busyNo)}
                        onClick={() => void runOrder(record, 'verify')}
                      >
                        {t('afdianVerify')}
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
