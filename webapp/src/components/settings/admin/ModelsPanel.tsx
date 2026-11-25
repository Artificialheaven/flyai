import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  activateProvider,
  createImageModel,
  createProvider,
  deleteImageModel,
  deleteProvider,
  discoverProviderModels,
  getConfig,
  shareImageModel,
  shareProvider,
  testProvider,
  updateImageModel,
  updateProvider,
  type AdminConfig,
  type ImageModelPayload,
  type ImageModelView,
  type ImageModelType,
  type PricePeriod,
  type ProviderPayload,
  type ProviderView,
  type TokenPricing,
} from '../../../services/flyai/admin.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { setActiveProviderId, useSettings } from '../../../services/settings.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { Select } from '../../base/Select.js'
import { showToast } from '../../base/toastState.js'
import { ModelBillingDialog } from '../ModelBillingDialog.js'
import { errorText, PanelEmpty, PanelError, Toggle } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

type Tab = 'llm' | 'image'
type DeleteTarget = { kind: Tab; id: string; name: string }

/** 我的模型：所有人可选共享模型；新建/编辑/删除/测试/共享仅管理员 */
export const ModelsPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const activeProviderId = useSettings((state) => state.activeProviderId)
  const [tab, setTab] = useState<Tab>('llm')
  const [providers, setProviders] = useState<ProviderView[]>([])
  const [imageModels, setImageModels] = useState<ImageModelView[]>([])
  const [globalActive, setGlobalActive] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [editingProvider, setEditingProvider] = useState<ProviderView | 'new' | null>(null)
  const [editingImage, setEditingImage] = useState<ImageModelView | 'new' | null>(null)
  const [sharingProvider, setSharingProvider] = useState<ProviderView | null>(null)
  const [sharingImage, setSharingImage] = useState<ImageModelView | null>(null)
  const [billingProvider, setBillingProvider] = useState<ProviderView | null>(null)
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null)
  /** 普通用户：null=分组总览，否则进入某个分组（空串表示未分组） */
  const [groupView, setGroupView] = useState<string | null>(null)
  /** 管理端：按提供商合并后，哪些表头已展开 */
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({})
  /** 窄屏（手机）时次要操作收进「更多」菜单 */
  const [isNarrow, setIsNarrow] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches
  )
  const [moreOpenId, setMoreOpenId] = useState<string | null>(null)

  useEffect(() => {
    const query = window.matchMedia('(max-width: 768px)')
    const update = () => setIsNarrow(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  /** 按 group 聚合：空串统一归入「未分组」，分组名按字母序，未分组置顶 */
  const groups = useMemo(() => {
    const map = new Map<string, ProviderView[]>()
    for (const provider of providers) {
      const key = (provider.group ?? '').trim()
      const list = map.get(key) ?? []
      list.push(provider)
      map.set(key, list)
    }
    return [...map.entries()].sort((a, b) => {
      if (!a[0] && b[0]) {
        return -1
      }
      if (a[0] && !b[0]) {
        return 1
      }
      return a[0].localeCompare(b[0], 'zh-Hans-CN')
    })
  }, [providers])
  const groupLabel = (group: string) => group || t('modelsGroupUngrouped')

  /** 管理端：同一提供商（同 base_url + 同 key）的模型合并成一组，点击表头展开 */
  const providerGroups = useMemo(() => {
    const map = new Map<string, { key: string; base_url: string; models: ProviderView[] }>()
    for (const provider of providers) {
      const key =
        provider.provider_key || `${provider.base_url ?? ''}\n${provider.api_key ?? ''}`
      const entry = map.get(key) ?? {
        key,
        base_url: provider.base_url ?? '',
        models: [] as ProviderView[],
      }
      entry.models.push(provider)
      map.set(key, entry)
    }
    return [...map.values()]
  }, [providers])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config: AdminConfig = await getConfig()
      const nextProviders = config.providers ?? []
      setProviders(nextProviders)
      setImageModels(config.image_models ?? [])
      setGlobalActive(config.active_provider ?? '')
      // 本地选择可能指向已被隐藏/删除的模型：清空，避免发送时 403
      const current = useSettings.getState().activeProviderId
      if (current && !nextProviders.some((p) => p.id === current)) {
        setActiveProviderId(null)
      }
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const activate = async (provider: ProviderView) => {
    if (busyId) {
      return
    }
    setBusyId(provider.id)
    setError('')
    try {
      // 管理员调用会改全局默认；普通用户只做可见性校验，选择记在本地
      await activateProvider(provider.id)
      setActiveProviderId(provider.id)
      showToast({ key: 'modelsActiveToast' })
      if (isAdmin) {
        setGlobalActive(provider.id)
      }
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const test = async (provider: ProviderView) => {
    setBusyId(provider.id)
    setError('')
    try {
      await testProvider(provider.id)
      showToast({ key: 'modelsTestOk' })
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const confirmDelete = async () => {
    if (!deleting) {
      return
    }
    const target = deleting
    setBusyId(target.id)
    setError('')
    try {
      if (target.kind === 'llm') {
        await deleteProvider(target.id)
        if (activeProviderId === target.id) {
          setActiveProviderId(null)
        }
      } else {
        await deleteImageModel(target.id)
      }
      setDeleting(null)
      showToast({ key: 'adminDeleted' })
      await load()
    } catch (err) {
      setError(t('adminDeleteFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }


  const renderProviderItem = (provider: ProviderView) => {
    const active = provider.id === activeProviderId
    const global = !activeProviderId && provider.id === globalActive
    return (
      <div
        key={provider.id}
        className={clsx(styles.item, (active || global) && styles.itemActive)}
      >
        <div className={styles.itemMain}>
          <div className={styles.itemName}>
            <span>{provider.id}</span>
            {provider.group ? <span className={styles.badge}>{provider.group}</span> : null}
            {active ? (
              <span className={clsx(styles.badge, styles.badgeBrand)}>{t('adminActive')}</span>
            ) : global ? (
              <span className={clsx(styles.badge, styles.badgeBrand)}>
                {t('modelsGlobalDefault')}
              </span>
            ) : null}
            {provider.shared ? (
              <span className={styles.badge}>{t('adminShared')}</span>
            ) : null}
            {provider.shared && (provider.min_level ?? 0) > 0 ? (
              <span className={styles.badge}>
                {t('modelsMinLevelBadge', { level: provider.min_level ?? 0 })}
              </span>
            ) : null}
          </div>
          <div className={styles.itemMeta}>
            {provider.model || provider.id}
            {provider.base_url ? ` · ${provider.base_url}` : ''}
          </div>
        </div>
        <div className={styles.itemActions}>
          <Button
            size="s"
            variant={active ? 'outlined' : 'primary'}
            disabled={active || busyId === provider.id}
            onClick={() => void activate(provider)}
          >
            {t(active ? 'adminActive' : 'adminUse')}
          </Button>
          <Button size="s" variant="outlined" onClick={() => setBillingProvider(provider)}>
            {t('modelsBilling')}
          </Button>
          {isAdmin ? (
            <Button
              size="s"
              variant="outlined"
              disabled={busyId === provider.id}
              onClick={() => void test(provider)}
            >
              {t('adminTest')}
            </Button>
          ) : null}
          {isAdmin && !isNarrow ? (
            <>
              <Button size="s" variant="outlined" onClick={() => setEditingProvider(provider)}>
                {t('adminEdit')}
              </Button>
              <Button size="s" variant="outlined" onClick={() => setSharingProvider(provider)}>
                {t('modelsShare')}
              </Button>
              <Button
                size="s"
                variant="dangerOutlined"
                onClick={() => setDeleting({ kind: 'llm', id: provider.id, name: provider.id })}
              >
                {t('adminDelete')}
              </Button>
            </>
          ) : null}
          {isAdmin && isNarrow ? (
            <div className={styles.moreWrap}>
              <Button
                size="s"
                variant="outlined"
                aria-label={t('modelsMoreActions')}
                aria-haspopup="menu"
                aria-expanded={moreOpenId === provider.id}
                onClick={() =>
                  setMoreOpenId((prev) => (prev === provider.id ? null : provider.id))
                }
              >
                ⋯
              </Button>
              {moreOpenId === provider.id ? (
                <>
                  <div className={styles.moreBackdrop} onClick={() => setMoreOpenId(null)} />
                  <div className={styles.moreList} role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.moreItem}
                      onClick={() => {
                        setMoreOpenId(null)
                        void test(provider)
                      }}
                    >
                      {t('adminTest')}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.moreItem}
                      onClick={() => {
                        setMoreOpenId(null)
                        setEditingProvider(provider)
                      }}
                    >
                      {t('adminEdit')}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.moreItem}
                      onClick={() => {
                        setMoreOpenId(null)
                        setSharingProvider(provider)
                      }}
                    >
                      {t('modelsShare')}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className={clsx(styles.moreItem, styles.moreItemDanger)}
                      onClick={() => {
                        setMoreOpenId(null)
                        setDeleting({ kind: 'llm', id: provider.id, name: provider.id })
                      }}
                    >
                      {t('adminDelete')}
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideModelsOptionLabel')}</div>
          <div className={styles.subtitle}>{t('modelsSubtitle')}</div>
        </div>
        {isAdmin ? (
          <Button
            size="s"
            onClick={() =>
              tab === 'llm' ? setEditingProvider('new') : setEditingImage('new')
            }
          >
            {t('modelsCreate')}
          </Button>
        ) : null}
      </div>
      {error ? <PanelError message={error} /> : null}

      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'llm'}
          className={clsx(styles.tab, tab === 'llm' && styles.tabActive)}
          onClick={() => setTab('llm')}
        >
          {t('modelsTabLlm', { count: providers.length })}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'image'}
          className={clsx(styles.tab, tab === 'image' && styles.tabActive)}
          onClick={() => setTab('image')}
        >
          {t('modelsTabImage', { count: imageModels.length })}
        </button>
      </div>

      {loading ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : tab === 'llm' ? (
        !isAdmin && groupView === null ? (
          <div className={styles.groupGrid}>
            {groups.map(([group, list]) => (
              <button
                key={group || '__ungrouped'}
                type="button"
                className={styles.groupCard}
                onClick={() => setGroupView(group)}
              >
                <span className={styles.groupCardName}>{groupLabel(group)}</span>
                <span className={styles.groupCardMeta}>
                  {t('modelsGroupCount', { count: list.length })}
                </span>
              </button>
            ))}
          </div>
        ) : providers.length === 0 ? (
          <PanelEmpty text={t('modelsEmpty')} />
        ) : isAdmin ? (
          <div className={styles.list}>
            {providerGroups.map((group) => {
              const open = expandedProviders[group.key] ?? providerGroups.length === 1
              return (
                <div key={group.key} className={styles.providerGroup}>
                  <button
                    type="button"
                    className={styles.providerHead}
                    onClick={() =>
                      setExpandedProviders((prev) => ({ ...prev, [group.key]: !open }))
                    }
                  >
                    <span className={styles.providerName}>
                      {group.base_url || t('modelsProviderUnknown')}
                    </span>
                    <span className={styles.providerMeta} title={group.base_url}>
                      {group.models[0]?.api_key ? `${group.models[0].api_key} · ` : ''}
                      {t('modelsProviderCount', { count: group.models.length })}
                    </span>
                    <span className={styles.chevron}>{open ? '▾' : '▸'}</span>
                  </button>
                  {open ? (
                    <div className={styles.providerModels}>
                      {group.models.map((provider) => renderProviderItem(provider))}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : (
          <div className={styles.list}>
            <button type="button" className={styles.backLink} onClick={() => setGroupView(null)}>
              ← {t('modelsBackToGroups')}
            </button>
            {providers
              .filter((provider) => (provider.group ?? '').trim() === groupView)
              .map((provider) => renderProviderItem(provider))}
          </div>
        )
      ) : imageModels.length === 0 ? (
        <PanelEmpty text={t('modelsEmptyImage')} />
      ) : (
        <div className={styles.list}>
          {imageModels.map((model) => (
            <div key={model.id} className={styles.item}>
              <div className={styles.itemMain}>
                <div className={styles.itemName}>
                  <span>{model.name || model.id}</span>
                  {model.shared ? (
                    <span className={styles.badge}>{t('adminShared')}</span>
                  ) : null}
                  {model.shared && (model.min_level ?? 0) > 0 ? (
                    <span className={styles.badge}>
                      {t('modelsMinLevelBadge', { level: model.min_level ?? 0 })}
                    </span>
                  ) : null}
                </div>
                <div className={styles.itemMeta}>
                  {model.model}
                  {model.base_url ? ` · ${model.base_url}` : ''}
                  {model.type === 'openai'
                    ? ` · ${t('modelsImageType_openai')}`
                    : model.type === 'google'
                      ? ` · ${t('modelsImageType_google')}`
                      : ''}
                </div>
              </div>
              <div className={styles.itemActions}>
                {isAdmin ? (
                  <>
                    <Button
                      size="s"
                      variant="outlined"
                      onClick={() => setEditingImage(model)}
                    >
                      {t('adminEdit')}
                    </Button>
                    <Button
                      size="s"
                      variant="outlined"
                      onClick={() => setSharingImage(model)}
                    >
                      {t('modelsShare')}
                    </Button>
                    <Button
                      size="s"
                      variant="dangerOutlined"
                      onClick={() =>
                        setDeleting({
                          kind: 'image',
                          id: model.id,
                          name: model.name || model.id,
                        })
                      }
                    >
                      {t('adminDelete')}
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}

      {editingProvider !== null ? (
        <ProviderForm
          provider={editingProvider === 'new' ? null : editingProvider}
          existingIds={providers.map((item) => item.id)}
          onClose={() => setEditingProvider(null)}
          onSaved={() => {
            setEditingProvider(null)
            showToast({ key: 'adminSaved' })
            void load()
          }}
        />
      ) : null}
      {editingImage !== null ? (
        <ImageForm
          model={editingImage === 'new' ? null : editingImage}
          onClose={() => setEditingImage(null)}
          onSaved={() => {
            setEditingImage(null)
            showToast({ key: 'adminSaved' })
            void load()
          }}
        />
      ) : null}
      {sharingProvider ? (
        <ShareForm
          name={sharingProvider.id}
          shared={Boolean(sharingProvider.shared)}
          minLevel={sharingProvider.min_level ?? 0}
          onClose={() => setSharingProvider(null)}
          onSave={async (shared, minLevel) => {
            await shareProvider(sharingProvider.id, { shared, min_level: minLevel })
            setSharingProvider(null)
            showToast({ key: 'adminSaved' })
            await load()
          }}
        />
      ) : null}
      {billingProvider ? (
        <ModelBillingDialog
          provider={billingProvider}
          onClose={() => setBillingProvider(null)}
        />
      ) : null}
      {sharingImage ? (
        <ShareForm
          name={sharingImage.name || sharingImage.id}
          shared={Boolean(sharingImage.shared)}
          minLevel={sharingImage.min_level ?? 0}
          onClose={() => setSharingImage(null)}
          onSave={async (shared, minLevel) => {
            await shareImageModel(sharingImage.id, { shared, min_level: minLevel })
            setSharingImage(null)
            showToast({ key: 'adminSaved' })
            await load()
          }}
        />
      ) : null}
      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => (busyId ? undefined : setDeleting(null))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busyId)}
                onClick={() => setDeleting(null)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                size="m"
                disabled={Boolean(busyId)}
                onClick={() => void confirmDelete()}
              >
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('modelsDeleteDesc', { name: deleting.name })}
        </Modal>
      ) : null}
    </div>
  )
}

/** 分时段价格的编辑草稿：全部用字符串保存，提交时统一校验与转换 */
type PeriodDraft = {
  name: string
  cache_hit_input: string
  input: string
  output: string
  daily_start: string
  daily_end: string
  date_start: string
  date_end: string
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

const toDraft = (period: PricePeriod): PeriodDraft => ({
  name: period.name ?? '',
  cache_hit_input: String(period.cache_hit_input ?? 0),
  input: String(period.input ?? 0),
  output: String(period.output ?? 0),
  daily_start: period.daily_start ?? '',
  daily_end: period.daily_end ?? '',
  date_start: period.date_start ?? '',
  date_end: period.date_end ?? '',
})

const emptyDraft = (): PeriodDraft => ({
  name: '',
  cache_hit_input: '0',
  input: '0',
  output: '0',
  daily_start: '',
  daily_end: '',
  date_start: '',
  date_end: '',
})

/** 空串按 0 处理；非法或负数返回 null */
const parsePrice = (text: string): number | null => {
  const trimmed = text.trim()
  const value = trimmed ? Number(trimmed) : 0
  return Number.isFinite(value) && value >= 0 ? value : null
}

/** 新建 / 编辑对话模型。api_key 留空表示保持原值（后端会按掩码识别）。 */
const ProviderForm = ({
  provider,
  existingIds,
  onClose,
  onSaved,
}: {
  provider: ProviderView | null
  /** 已存在的模型 ID，批量创建时用于生成唯一 ID */
  existingIds: string[]
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const [id, setId] = useState(provider?.id ?? '')
  const [model, setModel] = useState(provider?.model ?? '')
  const [type, setType] = useState<'openai' | 'anthropic'>(
    provider?.type === 'anthropic' ? 'anthropic' : 'openai'
  )
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [params, setParams] = useState(
    provider?.params && Object.keys(provider.params).length > 0
      ? JSON.stringify(provider.params, null, 2)
      : ''
  )
  const [vision, setVision] = useState(Boolean(provider?.vision))
  const [rate, setRate] = useState(String(provider?.rate ?? 1))
  const [pricingEnabled, setPricingEnabled] = useState(Boolean(provider?.pricing))
  const [priceHit, setPriceHit] = useState(String(provider?.pricing?.cache_hit_input ?? 0))
  const [priceInput, setPriceInput] = useState(String(provider?.pricing?.input ?? 0))
  const [priceOutput, setPriceOutput] = useState(String(provider?.pricing?.output ?? 0))
  const [periods, setPeriods] = useState<PeriodDraft[]>(() =>
    (provider?.pricing?.periods ?? []).map(toDraft)
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [group, setGroup] = useState(provider?.group ?? '')
  /** 新建时可先拉取上游模型列表，勾选后批量建模型 */
  const [discovering, setDiscovering] = useState(false)
  const [discovered, setDiscovered] = useState<string[]>([])
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [rowGroups, setRowGroups] = useState<Record<string, string>>({})
  const [batchGroup, setBatchGroup] = useState('')
  const selectedIds = discovered.filter((id) => selected[id])

  const fetchModels = async () => {
    if (discovering) {
      return
    }
    if (!baseUrl.trim()) {
      setError(t('modelsDiscoverRequired'))
      return
    }
    setDiscovering(true)
    setError('')
    try {
      const ids = await discoverProviderModels({
        type,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      })
      setDiscovered(ids)
      setSelected(Object.fromEntries(ids.map((id) => [id, false])))
      if (ids.length === 0) {
        setError(t('modelsDiscoverEmpty'))
      }
    } catch (err) {
      setError(t('modelsFetchFailed', { message: errorText(err) }))
    } finally {
      setDiscovering(false)
    }
  }

  const applyBatchGroup = () => {
    const value = batchGroup.trim()
    if (!value) {
      return
    }
    setRowGroups((prev) => {
      const next = { ...prev }
      for (const id of selectedIds) {
        next[id] = value
      }
      return next
    })
  }

  /** 用模型名生成合法且不重复的 provider id */
  const makeId = (raw: string, used: Set<string>): string => {
    let base = raw
      .trim()
      .replace(/[^A-Za-z0-9._-]/g, '-')
      .replace(/^[^A-Za-z0-9]+/, '')
    if (!base) {
      base = 'model'
    }
    let candidate = base
    let index = 2
    while (used.has(candidate)) {
      candidate = `${base}-${index++}`
    }
    used.add(candidate)
    return candidate
  }

  const updatePeriod = (index: number, patch: Partial<PeriodDraft>) =>
    setPeriods((list) => list.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  const addPeriod = () => setPeriods((list) => [...list, emptyDraft()])
  const removePeriod = (index: number) =>
    setPeriods((list) => list.filter((_, i) => i !== index))

  const submit = async () => {
    if (busy) {
      return
    }
    const batchMode = !provider && selectedIds.length > 0
    const trimmedModel = model.trim()
    if (!batchMode && !trimmedModel) {
      setError(t('modelsFieldModelRequired'))
      return
    }
    let parsed: Record<string, unknown> = {}
    if (params.trim()) {
      try {
        const raw: unknown = JSON.parse(params)
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
          throw new Error('not an object')
        }
        parsed = raw as Record<string, unknown>
      } catch {
        setError(t('modelsParamsInvalid'))
        return
      }
    }
    const rateValue = rate.trim() ? Number(rate) : null
    if (rateValue !== null && (!Number.isFinite(rateValue) || rateValue < 0)) {
      setError(t('modelsRateInvalid'))
      return
    }
    let pricing: TokenPricing | null = null
    if (pricingEnabled) {
      const cacheHitInput = parsePrice(priceHit)
      const input = parsePrice(priceInput)
      const output = parsePrice(priceOutput)
      if (cacheHitInput === null || input === null || output === null) {
        setError(t('modelsPricingInvalid'))
        return
      }
      const periodPayload: PricePeriod[] = []
      for (const draft of periods) {
        const hit = parsePrice(draft.cache_hit_input)
        const inputValue = parsePrice(draft.input)
        const outputValue = parsePrice(draft.output)
        if (hit === null || inputValue === null || outputValue === null) {
          setError(t('modelsPricingInvalid'))
          return
        }
        const dailyStart = draft.daily_start.trim()
        const dailyEnd = draft.daily_end.trim()
        // 时间窗需成对填写；跨午夜由后端按 start > end 自动识别
        if ((dailyStart || dailyEnd) && !(TIME_RE.test(dailyStart) && TIME_RE.test(dailyEnd))) {
          setError(t('modelsPricingTimeInvalid'))
          return
        }
        const dateStart = draft.date_start.trim()
        const dateEnd = draft.date_end.trim()
        if ((dateStart && !DATE_RE.test(dateStart)) || (dateEnd && !DATE_RE.test(dateEnd))) {
          setError(t('modelsPricingDateInvalid'))
          return
        }
        periodPayload.push({
          name: draft.name.trim(),
          cache_hit_input: hit,
          input: inputValue,
          output: outputValue,
          daily_start: dailyStart,
          daily_end: dailyEnd,
          date_start: dateStart,
          date_end: dateEnd,
        })
      }
      pricing = { cache_hit_input: cacheHitInput, input, output, periods: periodPayload }
    }
    const basePayload: Omit<ProviderPayload, 'id' | 'model' | 'group'> = {
      type,
      base_url: baseUrl.trim(),
      api_key: apiKey.trim(),
      params: parsed,
      vision,
      rate: rateValue,
      // 关闭时：原本按 token 计费的模型发 {} 以取消；本就按倍率的发 null 保持原值
      pricing: pricingEnabled ? pricing : provider?.pricing ? {} : null,
    }
    setBusy(true)
    setError('')
    try {
      if (batchMode) {
        const used = new Set(existingIds)
        for (const modelId of selectedIds) {
          await createProvider({
            ...basePayload,
            id: makeId(modelId, used),
            model: modelId,
            group: (rowGroups[modelId] ?? batchGroup).trim(),
          })
        }
      } else if (provider) {
        await updateProvider(provider.id, {
          ...basePayload,
          model: trimmedModel,
          group: group.trim(),
        })
      } else {
        await createProvider({
          ...basePayload,
          id: id.trim() || undefined,
          model: trimmedModel,
          group: group.trim(),
        })
      }
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t(provider ? 'modelsEditTitle' : 'modelsCreateTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void submit()}>
            {t(busy ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      }
    >
      {error ? <PanelError message={error} /> : null}
      {provider ? null : (
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('modelsFieldId')}</span>
          <input
            className={styles.input}
            value={id}
            placeholder={t('modelsFieldIdPlaceholder')}
            onChange={(event) => setId(event.target.value)}
          />
          <span className={styles.hint}>{t('modelsFieldIdHint')}</span>
        </label>
      )}
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldModel')}</span>
        <input
          className={styles.input}
          value={model}
          placeholder={t('modelsFieldModelPlaceholder')}
          onChange={(event) => setModel(event.target.value)}
        />
      </label>
      <div className={styles.inlineFields}>
        <div className={styles.field}>
          <span className={styles.fieldLabel}>{t('modelsFieldType')}</span>
          <Select<'openai' | 'anthropic'>
            value={type}
            ariaLabel={t('modelsFieldType')}
            options={[
              { value: 'openai', label: t('modelsTypeOpenai') },
              { value: 'anthropic', label: t('modelsTypeAnthropic') },
            ]}
            onChange={setType}
          />
        </div>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('modelsFieldRate')}</span>
          <input
            className={styles.input}
            value={rate}
            inputMode="decimal"
            onChange={(event) => setRate(event.target.value)}
          />
        </label>
      </div>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldBaseUrl')}</span>
        <input
          className={styles.input}
          value={baseUrl}
          placeholder="https://api.example.com/v1"
          onChange={(event) => setBaseUrl(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldApiKey')}</span>
        <input
          className={styles.input}
          value={apiKey}
          placeholder={provider ? t('modelsFieldApiKeyKeep') : t('modelsFieldApiKeyPlaceholder')}
          onChange={(event) => setApiKey(event.target.value)}
        />
      </label>
      {provider ? null : (
        <div className={styles.discoverBox}>
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowLabel}>{t('modelsFetchModels')}</div>
              <div className={styles.rowDesc}>{t('modelsFetchHint')}</div>
            </div>
            <Button
              size="s"
              variant="outlined"
              disabled={discovering}
              onClick={() => void fetchModels()}
            >
              {t(discovering ? 'modelsFetching' : 'modelsFetchModels')}
            </Button>
          </div>
          {discovered.length > 0 ? (
            <>
              <div className={styles.discoverHead}>
                <span className={styles.hint}>
                  {t('modelsSelected', { count: selectedIds.length })}
                </span>
                <div className={styles.discoverBatch}>
                  <input
                    className={styles.input}
                    value={batchGroup}
                    placeholder={t('modelsBatchGroup')}
                    onChange={(event) => setBatchGroup(event.target.value)}
                  />
                  <Button
                    size="s"
                    variant="outlined"
                    disabled={selectedIds.length === 0}
                    onClick={applyBatchGroup}
                  >
                    {t('modelsBatchGroupApply')}
                  </Button>
                </div>
              </div>
              <div className={styles.discoverList}>
                {discovered.map((modelId) => (
                  <label key={modelId} className={styles.discoverRow}>
                    <input
                      type="checkbox"
                      checked={Boolean(selected[modelId])}
                      onChange={(event) =>
                        setSelected((prev) => ({ ...prev, [modelId]: event.target.checked }))
                      }
                    />
                    <span className={styles.discoverName}>{modelId}</span>
                    <input
                      className={styles.input}
                      value={rowGroups[modelId] ?? ''}
                      placeholder={t('modelsGroupPlaceholder')}
                      onChange={(event) =>
                        setRowGroups((prev) => ({ ...prev, [modelId]: event.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              <span className={styles.hint}>{t('modelsGroupHint')}</span>
            </>
          ) : null}
        </div>
      )}
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldGroup')}</span>
        <input
          className={styles.input}
          value={group}
          placeholder={t('modelsGroupPlaceholder')}
          onChange={(event) => setGroup(event.target.value)}
        />
        <span className={styles.hint}>{t('modelsGroupHint')}</span>
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldParams')}</span>
        <textarea
          className={styles.textarea}
          value={params}
          placeholder='{"temperature": 0.8}'
          onChange={(event) => setParams(event.target.value)}
        />
      </label>
      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={vision}
          onChange={(event) => setVision(event.target.checked)}
        />
        {t('modelsFieldVision')}
      </label>
      <span className={styles.hint}>{t('modelsFieldRateHint')}</span>

      <div className={styles.row}>
        <div className={styles.rowText}>
          <div className={styles.rowLabel}>{t('modelsFieldPricing')}</div>
          <div className={styles.rowDesc}>{t('modelsFieldPricingDesc')}</div>
        </div>
        <Toggle
          checked={pricingEnabled}
          label={t('modelsFieldPricing')}
          onChange={setPricingEnabled}
        />
      </div>
      {pricingEnabled ? (
        <div className={styles.pricingBox}>
          <div className={styles.sectionTitle}>{t('modelsPricingTitle')}</div>
          <div className={styles.priceFields}>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('modelsPricingHit')}</span>
              <input
                className={styles.input}
                inputMode="decimal"
                value={priceHit}
                onChange={(event) => setPriceHit(event.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('modelsPricingInput')}</span>
              <input
                className={styles.input}
                inputMode="decimal"
                value={priceInput}
                onChange={(event) => setPriceInput(event.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('modelsPricingOutput')}</span>
              <input
                className={styles.input}
                inputMode="decimal"
                value={priceOutput}
                onChange={(event) => setPriceOutput(event.target.value)}
              />
            </label>
          </div>

          <div className={styles.periodsHead}>
            <div>
              <div className={styles.sectionTitle}>{t('modelsPricingPeriods')}</div>
              <div className={styles.hint}>{t('modelsPricingPeriodsHint')}</div>
            </div>
            <Button variant="outlined" size="s" onClick={addPeriod}>
              {t('modelsPricingAddPeriod')}
            </Button>
          </div>
          {periods.map((period, index) => (
            <div key={index} className={styles.periodCard}>
              <div className={styles.periodHead}>
                <input
                  className={styles.input}
                  value={period.name}
                  placeholder={`${t('modelsPricingPeriodName')} ${index + 1}`}
                  onChange={(event) => updatePeriod(index, { name: event.target.value })}
                />
                <Button
                  variant="dangerOutlined"
                  size="s"
                  onClick={() => removePeriod(index)}
                >
                  {t('modelsPricingRemove')}
                </Button>
              </div>
              <div className={styles.priceFields}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{t('modelsPricingHit')}</span>
                  <input
                    className={styles.input}
                    inputMode="decimal"
                    value={period.cache_hit_input}
                    onChange={(event) =>
                      updatePeriod(index, { cache_hit_input: event.target.value })
                    }
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{t('modelsPricingInput')}</span>
                  <input
                    className={styles.input}
                    inputMode="decimal"
                    value={period.input}
                    onChange={(event) => updatePeriod(index, { input: event.target.value })}
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{t('modelsPricingOutput')}</span>
                  <input
                    className={styles.input}
                    inputMode="decimal"
                    value={period.output}
                    onChange={(event) => updatePeriod(index, { output: event.target.value })}
                  />
                </label>
              </div>
              <div className={styles.inlineFields}>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>{t('modelsPricingDaily')}</span>
                  <div className={styles.pair}>
                    <input
                      className={styles.input}
                      placeholder="00:00"
                      value={period.daily_start}
                      onChange={(event) =>
                        updatePeriod(index, { daily_start: event.target.value })
                      }
                    />
                    <span className={styles.pairSep}>–</span>
                    <input
                      className={styles.input}
                      placeholder="08:00"
                      value={period.daily_end}
                      onChange={(event) =>
                        updatePeriod(index, { daily_end: event.target.value })
                      }
                    />
                  </div>
                  <span className={styles.hint}>{t('modelsPricingDailyHint')}</span>
                </div>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>{t('modelsPricingDates')}</span>
                  <div className={styles.pair}>
                    <input
                      className={styles.input}
                      placeholder="2026-01-01"
                      value={period.date_start}
                      onChange={(event) =>
                        updatePeriod(index, { date_start: event.target.value })
                      }
                    />
                    <span className={styles.pairSep}>–</span>
                    <input
                      className={styles.input}
                      placeholder="2026-12-31"
                      value={period.date_end}
                      onChange={(event) => updatePeriod(index, { date_end: event.target.value })}
                    />
                  </div>
                  <span className={styles.hint}>{t('modelsPricingDatesHint')}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Modal>
  )
}

/** 新建 / 编辑生图模型。 */
const ImageForm = ({
  model,
  onClose,
  onSaved,
}: {
  model: ImageModelView | null
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const [id, setId] = useState(model?.id ?? '')
  const [name, setName] = useState(model?.name ?? '')
  const [modelName, setModelName] = useState(model?.model ?? '')
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [type, setType] = useState<ImageModelType>(model?.type ?? 'ark')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const typeDefaults: Record<ImageModelType, { baseUrl: string; model: string }> = {
    ark: {
      baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
      model: 'doubao-seedream-5-0-260128',
    },
    openai: { baseUrl: 'https://api.openai.com/v1', model: 'gpt-image-1' },
    google: {
      baseUrl: 'https://generativelanguage.googleapis.com/v1beta',
      model: 'gemini-2.5-flash-image',
    },
  }

  const changeType = (next: ImageModelType) => {
    setType(next)
    if (!baseUrl.trim()) {
      setBaseUrl(typeDefaults[next].baseUrl)
    }
    if (!modelName.trim()) {
      setModelName(typeDefaults[next].model)
    }
  }

  const submit = async () => {
    if (busy) {
      return
    }
    if (!name.trim() || !modelName.trim()) {
      setError(t('modelsImageRequired'))
      return
    }
    const payload: ImageModelPayload = {
      name: name.trim(),
      type,
      model: modelName.trim() || typeDefaults[type].model,
      base_url: baseUrl.trim() || typeDefaults[type].baseUrl,
      api_key: apiKey.trim(),
    }
    setBusy(true)
    setError('')
    try {
      if (model) {
        await updateImageModel(model.id, payload)
      } else {
        await createImageModel({ ...payload, id: id.trim() || undefined })
      }
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t(model ? 'modelsEditImageTitle' : 'modelsCreateImageTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void submit()}>
            {t(busy ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      }
    >
      {error ? <PanelError message={error} /> : null}
      {model ? null : (
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('modelsFieldId')}</span>
          <input
            className={styles.input}
            value={id}
            placeholder={t('modelsFieldIdPlaceholder')}
            onChange={(event) => setId(event.target.value)}
          />
        </label>
      )}
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldName')}</span>
        <input
          className={styles.input}
          value={name}
          placeholder={t('modelsFieldImageNamePlaceholder')}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldModel')}</span>
        <input
          className={styles.input}
          value={modelName}
          placeholder={typeDefaults[type].model}
          onChange={(event) => setModelName(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldImageType')}</span>
        <Select<ImageModelType>
          value={type}
          ariaLabel={t('modelsFieldImageType')}
          options={[
            { value: 'ark', label: t('modelsImageTypeArk') },
            { value: 'openai', label: t('modelsImageTypeOpenai') },
            { value: 'google', label: t('modelsImageTypeGoogle') },
          ]}
          onChange={changeType}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldBaseUrl')}</span>
        <input
          className={styles.input}
          value={baseUrl}
          placeholder={typeDefaults[type].baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsFieldApiKey')}</span>
        <input
          className={styles.input}
          value={apiKey}
          placeholder={model ? t('modelsFieldApiKeyKeep') : t('modelsFieldApiKeyPlaceholder')}
          onChange={(event) => setApiKey(event.target.value)}
        />
      </label>
    </Modal>
  )
}

/** 共享开关 + 最低权限等级（0-255）。 */
const ShareForm = ({
  name,
  shared,
  minLevel,
  onClose,
  onSave,
}: {
  name: string
  shared: boolean
  minLevel: number
  onClose: () => void
  onSave: (shared: boolean, minLevel: number) => Promise<void>
}) => {
  const t = useTranslate()
  const [enabled, setEnabled] = useState(shared)
  const [level, setLevel] = useState(String(minLevel))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (busy) {
      return
    }
    const parsed = Number(level)
    if (!Number.isInteger(parsed) || parsed < 0 || parsed > 255) {
      setError(t('modelsLevelInvalid'))
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSave(enabled, parsed)
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t('modelsShareTitle', { name })}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void submit()}>
            {t(busy ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      }
    >
      {error ? <PanelError message={error} /> : null}
      <div className={styles.row}>
        <div className={styles.rowText}>
          <div className={styles.rowLabel}>{t('modelsShareEnable')}</div>
          <div className={styles.rowDesc}>{t('modelsShareDesc')}</div>
        </div>
        <Toggle
          checked={enabled}
          label={t('modelsShareEnable')}
          onChange={setEnabled}
        />
      </div>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('modelsShareMinLevel')}</span>
        <input
          className={styles.input}
          value={level}
          inputMode="numeric"
          disabled={!enabled}
          onChange={(event) => setLevel(event.target.value)}
        />
        <span className={styles.hint}>{t('modelsShareMinLevelHint')}</span>
      </label>
    </Modal>
  )
}