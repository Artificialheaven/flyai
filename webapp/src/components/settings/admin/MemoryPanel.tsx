import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import type { TranslationKey } from '../../../i18n/index.js'
import {
  deleteMemory,
  listMemory,
  memoryLedger,
  memorySources,
  memoryStats,
  recallLogs,
  type LedgerGroup,
  type MemoryEntryWithChar,
  type MemorySource,
  type MemoryStats,
  type RecallLog,
} from '../../../services/flyai/memory.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { Select } from '../../base/Select.js'
import { showToast } from '../../base/toastState.js'
import { PanelEmpty, PanelError, errorText } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

type Tab = 'entries' | 'sources' | 'ledger' | 'recall'

const tabs: { value: Tab; label: TranslationKey }[] = [
  { value: 'entries', label: 'memoryTabEntries' },
  { value: 'sources', label: 'memoryTabSources' },
  { value: 'ledger', label: 'memoryTabLedger' },
  { value: 'recall', label: 'memoryTabRecall' },
]

const formatTime = (value: string | number | undefined): string => {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

/** 个人记忆管理：条目检索/删除、按角色统计、账本与召回日志。 */
export const MemoryPanel = () => {
  const t = useTranslate()
  const [tab, setTab] = useState<Tab>('entries')
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [sources, setSources] = useState<MemorySource[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [charId, setCharId] = useState('')
  const [query, setQuery] = useState('')
  const [includeSuperseded, setIncludeSuperseded] = useState(false)
  const [entries, setEntries] = useState<MemoryEntryWithChar[]>([])
  const [entriesLoading, setEntriesLoading] = useState(false)
  const [entriesError, setEntriesError] = useState('')
  const [deleting, setDeleting] = useState<MemoryEntryWithChar | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  const [ledgerChar, setLedgerChar] = useState('')
  const [groups, setGroups] = useState<LedgerGroup[] | null>(null)
  const [ledgerLoading, setLedgerLoading] = useState(false)

  const [logs, setLogs] = useState<RecallLog[] | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)

  const sourceName = useMemo(() => {
    const map = new Map<string, string>()
    sources.forEach((item) => map.set(item.char_id, item.char_name || item.char_id))
    return map
  }, [sources])

  const loadOverview = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextStats, nextSources] = await Promise.all([memoryStats(), memorySources()])
      setStats(nextStats)
      setSources(nextSources.sources)
      setLedgerChar((current) => current || nextSources.sources[0]?.char_id || '')
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const loadEntries = useCallback(async () => {
    setEntriesLoading(true)
    setEntriesError('')
    try {
      const data = await listMemory({
        charId: charId || undefined,
        q: query.trim() || undefined,
        includeSuperseded,
      })
      setEntries(data.entries)
    } catch (err) {
      setEntriesError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setEntriesLoading(false)
    }
  }, [charId, includeSuperseded, query, t])

  // 搜索输入去抖，避免每次按键都打接口
  useEffect(() => {
    const timer = window.setTimeout(() => void loadEntries(), 250)
    return () => window.clearTimeout(timer)
  }, [loadEntries])

  const loadLedger = useCallback(async () => {
    if (!ledgerChar) {
      setGroups(null)
      return
    }
    setLedgerLoading(true)
    try {
      const data = await memoryLedger(ledgerChar)
      setGroups(data.groups)
    } catch (err) {
      setEntriesError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLedgerLoading(false)
    }
  }, [ledgerChar, t])

  useEffect(() => {
    if (tab === 'ledger') {
      void loadLedger()
    }
  }, [tab, loadLedger])

  const loadLogs = useCallback(async () => {
    setLogsLoading(true)
    try {
      const data = await recallLogs()
      setLogs(data.logs)
    } catch (err) {
      setEntriesError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLogsLoading(false)
    }
  }, [t])

  useEffect(() => {
    if (tab === 'recall') {
      void loadLogs()
    }
  }, [tab, loadLogs])

  const confirmDelete = async () => {
    if (!deleting || deleteBusy) {
      return
    }
    setDeleteBusy(true)
    try {
      await deleteMemory(deleting.id, deleting.char_id)
      setDeleting(null)
      showToast({ key: 'memoryDeleted' })
      void loadEntries()
      void loadOverview()
    } catch (err) {
      setEntriesError(t('adminDeleteFailed', { message: errorText(err) }))
    } finally {
      setDeleteBusy(false)
    }
  }

  const charOptions = [
    { value: '', label: t('memoryFilterAll') },
    ...sources.map((item) => ({
      value: item.char_id,
      label: `${item.char_name || item.char_id}`,
    })),
  ]

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideMemoryOptionLabel')}</div>
          <div className={styles.subtitle}>{t('memorySubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void loadOverview()}>
            {t('adminRefresh')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}

      {stats ? (
        <div className={styles.stats}>
          <div className={styles.stat}>
            <div className={styles.statValue}>{stats.total.entries}</div>
            <div className={styles.statLabel}>{t('memoryStatTotal')}</div>
            <div className={styles.statHint}>{t('memoryStatLedgerHint', { count: stats.total.ledger })}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statValue}>{stats.total.active}</div>
            <div className={styles.statLabel}>{t('memoryStatActive')}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statValue}>{stats.total.superseded}</div>
            <div className={styles.statLabel}>{t('memoryStatSuperseded')}</div>
          </div>
        </div>
      ) : null}

      <div className={styles.tabs} role="tablist">
        {tabs.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={tab === item.value}
            className={`${styles.tab} ${tab === item.value ? styles.tabActive : ''}`}
            onClick={() => setTab(item.value)}
          >
            {t(item.label)}
          </button>
        ))}
      </div>

      {loading ? <PanelEmpty text={t('adminLoading')} /> : null}

      {tab === 'entries' && !loading ? (
        <>
          <div className={styles.filters}>
            <Select
              value={charId}
              ariaLabel={t('memoryFilterChar')}
              options={charOptions}
              onChange={setCharId}
            />
            <input
              className={styles.searchInput}
              value={query}
              placeholder={t('memorySearchPlaceholder')}
              onChange={(event) => setQuery(event.target.value)}
            />
            <label className={styles.checkField}>
              <input
                type="checkbox"
                checked={includeSuperseded}
                onChange={(event) => setIncludeSuperseded(event.target.checked)}
              />
              {t('memoryIncludeSuperseded')}
            </label>
          </div>
          {entriesError ? <PanelError message={entriesError} /> : null}
          {entriesLoading && entries.length === 0 ? (
            <PanelEmpty text={t('adminLoading')} />
          ) : entries.length === 0 ? (
            <PanelEmpty text={t('memoryEmpty')} />
          ) : (
            <div className={styles.list}>
              {entries.map((entry) => (
                <div key={`${entry.char_id}:${entry.id}`} className={styles.entry}>
                  <div className={styles.entryText}>{entry.text}</div>
                  <div className={styles.entryMeta}>
                    <span>{sourceName.get(entry.char_id) ?? entry.char_id}</span>
                    {entry.source ? <span>{t('memorySource', { source: entry.source })}</span> : null}
                    <span>{t('memoryImportance', { value: entry.importance })}</span>
                    <span>{t('memoryAccess', { count: entry.access_count })}</span>
                    <span>{formatTime(entry.created_at)}</span>
                    {entry.superseded ? <span className={styles.badge}>{t('memoryStatSuperseded')}</span> : null}
                  </div>
                  {entry.tags.length || entry.entities.length ? (
                    <div className={styles.chips}>
                      {entry.tags.map((tag) => (
                        <span key={`tag:${tag}`} className={styles.chip}>
                          #{tag}
                        </span>
                      ))}
                      {entry.entities.map((name) => (
                        <span key={`entity:${name}`} className={styles.chip}>
                          {name}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className={styles.entryActions}>
                    <Button variant="dangerOutlined" size="s" onClick={() => setDeleting(entry)}>
                      {t('adminDelete')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {tab === 'sources' && !loading ? (
        sources.length === 0 ? (
          <PanelEmpty text={t('memoryEmpty')} />
        ) : (
          <div className={styles.list}>
            {sources.map((item) => (
              <div key={`${item.char_id}:${item.session_id}`} className={styles.item}>
                <div className={styles.itemMain}>
                  <div className={styles.itemName}>
                    {item.char_name || item.char_id}
                    {item.orphan ? <span className={styles.badge}>{t('memoryOrphan')}</span> : null}
                  </div>
                  <div className={styles.itemMeta}>
                    {item.session_title || item.session_id}
                  </div>
                </div>
                <div className={styles.chips}>
                  <span className={styles.chip}>{t('memoryStatActive')} {item.active}</span>
                  <span className={styles.chip}>{t('memoryStatSuperseded')} {item.superseded}</span>
                  <span className={styles.chip}>{t('memoryStatLedger')} {item.ledger}</span>
                </div>
              </div>
            ))}
          </div>
        )
      ) : null}

      {tab === 'ledger' && !loading ? (
        <>
          <div className={styles.filters}>
            <Select
              value={ledgerChar}
              ariaLabel={t('memoryFilterChar')}
              placeholder={t('memoryLedgerPick')}
              options={sources.map((item) => ({
                value: item.char_id,
                label: item.char_name || item.char_id,
              }))}
              onChange={setLedgerChar}
            />
            <Button variant="outlined" size="s" disabled={ledgerLoading || !ledgerChar} onClick={() => void loadLedger()}>
              {t('adminRefresh')}
            </Button>
          </div>
          {!ledgerChar ? (
            <PanelEmpty text={t('memoryLedgerPick')} />
          ) : ledgerLoading ? (
            <PanelEmpty text={t('adminLoading')} />
          ) : !groups || groups.length === 0 ? (
            <PanelEmpty text={t('memoryLedgerEmpty')} />
          ) : (
            groups.map((group) => (
              <div key={group.kind}>
                <div className={styles.sectionTitle}>{group.kind}</div>
                <div className={styles.list}>
                  {group.items.map((item) => (
                    <div key={item.id} className={styles.item}>
                      <div className={styles.itemMain}>
                        <div className={styles.itemName}>
                          {item.name}
                          <span className={styles.badge}>{item.state}</span>
                          {item.status ? <span className={styles.badge}>{item.status}</span> : null}
                        </div>
                        <div className={styles.itemMeta}>
                          {t('memoryLedgerFirstSeen', { time: formatTime(item.first_seen) })}
                          {' · '}
                          {t('memoryLedgerUpdated', { time: formatTime(item.updated_at) })}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </>
      ) : null}

      {tab === 'recall' && !loading ? (
        <>
          <div className={styles.actions}>
            <Button variant="outlined" size="s" disabled={logsLoading} onClick={() => void loadLogs()}>
              {t('adminRefresh')}
            </Button>
          </div>
          {logsLoading && !logs ? (
            <PanelEmpty text={t('adminLoading')} />
          ) : !logs || logs.length === 0 ? (
            <PanelEmpty text={t('memoryRecallEmpty')} />
          ) : (
            <div className={styles.list}>
              {logs.map((log, index) => (
                <div key={`${log.ts}:${index}`} className={styles.entry}>
                  <div className={styles.entryMeta}>
                    <span>{formatTime(log.ts * 1000)}</span>
                    {log.mode ? <span className={styles.badge}>{log.mode}</span> : null}
                    {typeof log.count === 'number' ? (
                      <span>{t('memoryRecallCount', { count: log.count })}</span>
                    ) : null}
                  </div>
                  {log.query ? <div className={styles.entryText}>{log.query}</div> : null}
                  {log.items?.length ? (
                    <div className={styles.chips}>
                      {log.items.slice(0, 8).map((item, itemIndex) => (
                        <span key={itemIndex} className={styles.chip}>
                          {item.text ?? ''}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => {
            if (!deleteBusy) {
              setDeleting(null)
            }
          }}
          footer={
            <div className={styles.dialogFooter}>
              <Button variant="outlined" disabled={deleteBusy} onClick={() => setDeleting(null)}>
                {t('cancel')}
              </Button>
              <Button variant="danger" disabled={deleteBusy} onClick={() => void confirmDelete()}>
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('memoryDeleteDesc')}
        </Modal>
      ) : null}
    </div>
  )
}