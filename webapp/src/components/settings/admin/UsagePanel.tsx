import clsx from 'clsx'
import { useCallback, useEffect, useState } from 'react'
import { useTranslate, type TranslationKey } from '../../../i18n/index.js'
import {
  getMyQuota,
  getMyUsage,
  type MyUsage,
  type QuotaView,
} from '../../../services/flyai/admin.js'
import { listSessions } from '../../../services/flyai/sessions.js'
import { useAuth } from '../../../services/flyai/auth.js'
import {
  connectDeepSeek,
  getDeepSeekBalance,
  getDeepSeekStatus,
  type DeepSeekBalance,
  type DeepSeekStatus,
} from '../../../services/flyai/deepseek.js'
import type { SessionSummary } from '../../../services/flyai/types.js'
import { Button } from '../../base/Button.js'
import { errorText, PanelError, PanelEmpty } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

const formatNumber = (value: number): string => {
  const rounded = Math.round(value * 10) / 10
  return rounded.toLocaleString()
}

/** 点数（1 点 = 1 元）固定两位小数 */
const formatPoints = (value: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** 项目 kind → 文案键；未知 kind 原样展示，兼容后端后续新增项目 */
const KIND_LABELS: Record<string, TranslationKey> = {
  chat: 'usageKindChat',
  search: 'usageKindSearch',
  fetch: 'usageKindFetch',
  tts: 'usageKindTts',
  asr: 'usageKindAsr',
}

/** 后端计费单位是中文常量，前端按语言转一道；未知单位原样展示 */
const UNIT_LABELS: Record<string, TranslationKey> = {
  token: 'usageUnitTokens',
  次: 'usageUnitCalls',
  字符: 'usageUnitChars',
  秒: 'usageUnitSeconds',
}

type TokenStat = { prompt: number; completion: number; total: number }

const addUsage = (sum: TokenStat, session: SessionSummary): TokenStat => {
  const usage = session.token_usage
  const prompt = sum.prompt + (usage?.prompt_tokens ?? 0)
  const completion = sum.completion + (usage?.completion_tokens ?? 0)
  // 旧会话可能没有 total_tokens：用输入 + 输出兜底
  const total = sum.total + (usage?.total_tokens ?? (usage?.prompt_tokens ?? 0) + (usage?.completion_tokens ?? 0))
  return { prompt, completion, total }
}

/** 用量与额度：所有人可看自己的剩余点数、会话/消息/token 统计与近 7 天用量 */
export const UsagePanel = () => {
  const t = useTranslate()
  const [quota, setQuota] = useState<QuotaView | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [usage, setUsage] = useState<MyUsage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 点击柱状行固定悬浮框（触屏无 hover 时用）；再次点击或点其他行切换 */
  const [pinnedDate, setPinnedDate] = useState<string | null>(null)
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [dsStatus, setDsStatus] = useState<DeepSeekStatus | null>(null)
  const [dsBalances, setDsBalances] = useState<DeepSeekBalance[]>([])
  const [dsKey, setDsKey] = useState('')
  const [dsModel, setDsModel] = useState('deepseek-chat')
  const [dsBusy, setDsBusy] = useState(false)
  const [dsNotice, setDsNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextQuota, nextSessions, nextUsage] = await Promise.all([
        getMyQuota(),
        listSessions(),
        getMyUsage(7),
      ])
      setQuota(nextQuota)
      setSessions(nextSessions)
      setUsage(nextUsage)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const loadDeepSeek = useCallback(async () => {
    try {
      const status = await getDeepSeekStatus()
      setDsStatus(status)
      if (status.model) setDsModel(status.model)
      if (status.configured) {
        const balance = await getDeepSeekBalance().catch(() => null)
        setDsBalances(balance?.balances || [])
      } else {
        setDsBalances([])
      }
    } catch {
      // 非管理员/未配置时静默降级
    }
  }, [])

  useEffect(() => {
    void loadDeepSeek()
  }, [loadDeepSeek])

  // 管理员在平台登录后返回 FlyAI：自动打开 API Keys 页（官方没有自动创建 Key 的接口）
  useEffect(() => {
    const onFocus = () => {
      const at = Number(sessionStorage.getItem('flyai-deepseek-login') || 0)
      if (!at) return
      sessionStorage.removeItem('flyai-deepseek-login')
      if (Date.now() - at > 15 * 60 * 1000) return
      const url = dsStatus?.platform.api_keys
      if (!url) return
      const opened = window.open(url, '_blank', 'noopener')
      setDsNotice(opened ? t('usageDeepSeekOpenKeys') : t('usageDeepSeekOpenKeysManual'))
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [dsStatus, t])

  const loginDeepSeek = () => {
    const url = dsStatus?.platform.sign_in || 'https://platform.deepseek.com/sign_in'
    sessionStorage.setItem('flyai-deepseek-login', String(Date.now()))
    window.open(url, '_blank', 'noopener')
    setDsNotice(t('usageDeepSeekLoginHint'))
  }

  const connectDeepSeekKey = async () => {
    if (dsBusy || !dsKey.trim()) return
    setDsBusy(true)
    try {
      await connectDeepSeek(dsKey.trim(), dsModel, true)
      setDsKey('')
      setDsNotice(t('usageDeepSeekConnected'))
      await Promise.all([loadDeepSeek(), load()])
    } catch (err) {
      setDsNotice(errorText(err))
    } finally {
      setDsBusy(false)
    }
  }

  const tokens = sessions.reduce(addUsage, { prompt: 0, completion: 0, total: 0 })
  const messageCount = sessions.reduce((sum, item) => sum + (item.message_count ?? 0), 0)
  const days = usage?.days ?? []
  const maxTokens = days.reduce((max, day) => Math.max(max, day.total_tokens), 0)
  // 只有搜索/抓取/语音消耗的用户没有 token 行：cost 也要参与空状态判断
  const hasUsage = days.some((day) => day.total_tokens > 0 || day.messages > 0 || day.cost > 0)
  const categories = (usage?.categories ?? []).filter(
    (item) => item.cost > 0 || item.quantity > 0
  )
  const unlimited = Boolean(quota?.unlimited) || quota?.total === -1
  const total = quota ? Math.max(0, quota.total) : 0
  const used = quota?.used ?? 0
  const remaining = quota ? (quota.remaining ?? Math.max(0, total - used)) : 0
  const percent = !unlimited && total > 0 ? Math.min(100, (used / total) * 100) : 0
  const officialActive = Boolean(dsStatus?.is_official_active && dsBalances.length > 0)

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideUsageOptionLabel')}</div>
          <div className={styles.subtitle}>{t('usageSubtitle')}</div>
        </div>
        <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
          {t(loading ? 'adminLoading' : 'adminRefresh')}
        </Button>
      </div>
      {error ? <PanelError message={error} /> : null}

      {officialActive ? (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <span className={styles.rowLabel}>{t('usageDeepSeekOfficialBalance')}</span>
            <span className={clsx(styles.badge, styles.badgeBrand)}>
              {t('usageDeepSeekOfficialBadge')}
            </span>
          </div>
          {dsBalances.map((item) => (
            <div key={item.currency} className={styles.quotaValue}>
              {item.currency} {item.total}
            </div>
          ))}
          <div className={styles.hint}>{t('usageDeepSeekBalanceHint')}</div>
          <div className={styles.rowLine}>
            <a href={dsStatus?.platform.top_up} target="_blank" rel="noreferrer">
              {t('usageDeepSeekTopUp')}
            </a>
            <a href={dsStatus?.platform.usage} target="_blank" rel="noreferrer">
              {t('usageDeepSeekOfficialUsage')}
            </a>
          </div>
        </div>
      ) : null}

      {isAdmin ? (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <span className={styles.rowLabel}>{t('usageDeepSeekTitle')}</span>
            {dsStatus?.configured ? (
              <span className={styles.hint}>{dsStatus.key_masked}</span>
            ) : null}
          </div>
          <div className={styles.hint}>{t('usageDeepSeekHint')}</div>
          <div className={styles.rowLine}>
            <Button variant="outlined" size="s" onClick={loginDeepSeek}>
              {t('usageDeepSeekLogin')}
            </Button>
            <a href={dsStatus?.platform.api_keys} target="_blank" rel="noreferrer">
              {t('usageDeepSeekOpenKeys')}
            </a>
            <a href={dsStatus?.platform.top_up} target="_blank" rel="noreferrer">
              {t('usageDeepSeekTopUp')}
            </a>
          </div>
          <div className={styles.rowLine}>
            <input
              className={styles.textInput}
              type="password"
              value={dsKey}
              placeholder={t('usageDeepSeekKeyPlaceholder')}
              onChange={(event) => setDsKey(event.target.value)}
            />
            <select
              className={styles.textInput}
              value={dsModel}
              onChange={(event) => setDsModel(event.target.value)}
            >
              <option value="deepseek-chat">deepseek-chat</option>
              <option value="deepseek-reasoner">deepseek-reasoner</option>
            </select>
            <Button size="s" disabled={dsBusy || !dsKey.trim()} onClick={() => void connectDeepSeekKey()}>
              {dsBusy ? t('usageDeepSeekConnecting') : t('usageDeepSeekConnect')}
            </Button>
          </div>
          {dsNotice ? <div className={styles.hint}>{dsNotice}</div> : null}
        </div>
      ) : null}

      <div className={styles.card} style={officialActive ? { display: 'none' } : undefined}>
        <div className={styles.cardHead}>
          <span className={styles.rowLabel}>{t('usageQuotaTitle')}</span>
          {unlimited ? (
            <span className={clsx(styles.badge, styles.badgeBrand)}>
              {t('usageQuotaUnlimitedBadge')}
            </span>
          ) : null}
        </div>
        <div className={styles.quotaValue}>
          {quota ? (unlimited ? '∞' : formatPoints(remaining)) : '—'}
          {unlimited ? null : <span className={styles.quotaUnit}>{t('usagePointsUnit')}</span>}
        </div>
        <div className={styles.hint}>
          {unlimited
            ? t('usageQuotaUnlimitedHint')
            : t('usageQuotaUsed', {
                used: formatPoints(used),
                total: formatPoints(total),
              })}
        </div>
        {unlimited ? null : (
          <>
            <div className={styles.progress}>
              <div className={styles.progressBar} style={{ width: `${percent}%` }} />
            </div>
            <div className={styles.progressMeta}>
              <span>{t('usageQuotaRemaining', { count: formatPoints(remaining) })}</span>
              <span>{Math.round(percent)}%</span>
            </div>
          </>
        )}
      </div>

      <div className={styles.stats}>
        <div className={styles.stat}>
          <div className={styles.statValue}>{formatNumber(sessions.length)}</div>
          <div className={styles.statLabel}>{t('usageStatSessions')}</div>
        </div>
        <div className={styles.stat}>
          <div className={styles.statValue}>{formatNumber(messageCount)}</div>
          <div className={styles.statLabel}>{t('usageStatMessages')}</div>
        </div>
        <div className={styles.stat}>
          <div className={styles.statValue}>{formatNumber(tokens.total)}</div>
          <div className={styles.statLabel}>{t('usageStatTokens')}</div>
        </div>
      </div>

      {officialActive ? (
        <div className={styles.hint}>
          {t('usageDeepSeekTokenNote')}{' '}
          <a href={dsStatus?.platform.usage} target="_blank" rel="noreferrer">
            {t('usageDeepSeekOfficialUsage')}
          </a>
        </div>
      ) : null}
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <span className={styles.rowLabel}>{t('usageChartTitle')}</span>
          {usage ? (
            <span className={styles.hint}>
              {t('usageChartTotal', {
                tokens: formatNumber(usage.totals.total_tokens),
                cost: formatPoints(usage.totals.cost),
              })}
            </span>
          ) : null}
        </div>
        {!hasUsage ? (
          <PanelEmpty text={t('usageChartEmpty')} />
        ) : (
          <div className={styles.chart}>
            {days.map((day, index) => {
              // 输入拆成命中/未命中缓存两段，输出单独一段，按占比堆叠
              const hit = day.cached_tokens
              const miss = Math.max(0, day.prompt_tokens - day.cached_tokens)
              const output = day.completion_tokens
              const sum = hit + miss + output
              const barWidth =
                maxTokens > 0 && day.total_tokens > 0
                  ? Math.max(4, (day.total_tokens / maxTokens) * 100)
                  : 0
              const pinned = pinnedDate === day.date
              const isLast = index === days.length - 1
              return (
                <div
                  key={day.date}
                  className={clsx(styles.chartRow, pinned && styles.chartRowPinned)}
                  onClick={() => setPinnedDate(pinned ? null : day.date)}
                >
                  <span className={styles.chartLabel} title={day.date}>
                    {day.date.slice(5)}
                  </span>
                  <div className={styles.chartTrack}>
                    <div className={styles.chartBar} style={{ width: `${barWidth}%` }}>
                      {sum > 0 ? (
                        <>
                          {hit > 0 ? (
                            <span
                              className={clsx(styles.chartSeg, styles.chartBarHit)}
                              style={{ width: `${(hit / sum) * 100}%` }}
                            />
                          ) : null}
                          {miss > 0 ? (
                            <span
                              className={clsx(styles.chartSeg, styles.chartBarMiss)}
                              style={{ width: `${(miss / sum) * 100}%` }}
                            />
                          ) : null}
                          {output > 0 ? (
                            <span
                              className={clsx(styles.chartSeg, styles.chartBarOut)}
                              style={{ width: `${(output / sum) * 100}%` }}
                            />
                          ) : null}
                        </>
                      ) : null}
                    </div>
                  </div>
                  <span className={styles.chartValue}>
                    {formatNumber(day.total_tokens)} · {formatPoints(day.cost)}
                    {t('usagePointsUnit')}
                  </span>
                  <div className={clsx(styles.chartTip, isLast && styles.chartTipAbove)}>
                    <div className={styles.chartTipHead}>
                      <span>{day.date}</span>
                      <span>{formatNumber(day.total_tokens)}</span>
                    </div>
                    <div className={styles.chartTipRow}>
                      <span className={clsx(styles.chartSwatch, styles.chartBarHit)} />
                      <span className={styles.chartTipLabel}>{t('usageTipCacheHit')}</span>
                      <span className={styles.chartTipValue}>{formatNumber(hit)}</span>
                    </div>
                    <div className={styles.chartTipRow}>
                      <span className={clsx(styles.chartSwatch, styles.chartBarMiss)} />
                      <span className={styles.chartTipLabel}>{t('usageTipCacheMiss')}</span>
                      <span className={styles.chartTipValue}>{formatNumber(miss)}</span>
                    </div>
                    <div className={styles.chartTipRow}>
                      <span className={clsx(styles.chartSwatch, styles.chartBarOut)} />
                      <span className={styles.chartTipLabel}>{t('usageTipOutput')}</span>
                      <span className={styles.chartTipValue}>{formatNumber(output)}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <span className={styles.rowLabel}>{t('usageCategoryTitle')}</span>
          <span className={styles.hint}>{t('usageCategoryHint')}</span>
        </div>
        {categories.length === 0 ? (
          <PanelEmpty text={t('usageCategoryEmpty')} />
        ) : (
          <div className={styles.rows}>
            {categories.map((item) => {
              const labelKey = KIND_LABELS[item.kind]
              const unitKey = UNIT_LABELS[item.unit]
              return (
                <div key={item.kind} className={styles.row}>
                  <div className={styles.rowText}>
                    <div className={styles.rowLabel}>{labelKey ? t(labelKey) : item.kind}</div>
                    <div className={styles.rowDesc}>
                      {item.kind === 'chat'
                        ? t('usageCategoryChatDetail', {
                            tokens: formatNumber(item.quantity),
                            calls: formatNumber(item.calls),
                          })
                        : t('usageCategoryMeteredDetail', {
                            quantity: formatNumber(item.quantity),
                            unit: unitKey ? t(unitKey) : item.unit,
                            calls: formatNumber(item.calls),
                          })}
                    </div>
                  </div>
                  {item.engines.length > 0 ? (
                    <span className={styles.chip}>{item.engines.join(' / ')}</span>
                  ) : null}
                  <span className={styles.rowLabel}>
                    {formatPoints(item.cost)}
                    {t('usagePointsUnit')}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}