import clsx from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import {
  IconCheckedOutline14,
  IconCloseOutline16,
  IconLibraryOutline16,
  IconPanelLeftOutline16,
  IconSearchOutline16,
  IconSettingsOutline16,
  IconUsersOutline16,
  NewChatIcon,
} from '../base/icons.js'
import { setSiderCollapsed } from '../../services/settings.js'
import { groupSessions } from '../../services/chat/sessionGroups.js'
import {
  newChat,
  removeSession,
  removeSessions,
  searchSessionList,
  useChatStore,
} from '../../services/chat/store.js'
import type { SessionSearchResult } from '../../services/chat/store.js'
import type { SessionSummary } from '../../services/chat/types.js'
import { Button } from '../base/Button.js'
import { IconButton } from '../base/IconButton.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import { openSettings } from '../settings/settingsDialogState.js'
import { openLore } from '../lore/loreState.js'
import { openNewRoom } from '../room/newRoomState.js'
import { useAuth } from '../../services/flyai/auth.js'
import { closeMobileSider, useMobileSider } from './mobileSiderState.js'
import { BrandWordmark } from './BrandWordmark.js'
import { SessionItem } from './SessionItem.js'
import styles from './Sider.module.css'

/** 展开的侧边栏：名称与收起按钮、开启新对话、会话搜索/批量管理、按时间分组的列表、底部设置 */
export const Sider = ({ mobile = false }: { mobile?: boolean }) => {
  const t = useTranslate()
  const sessions = useChatStore((state) => state.sessions)
  const activeId = useChatStore((state) => state.activeId)
  const user = useAuth((state) => state.user)
  const [now, setNow] = useState(Date.now)
  const groups = useMemo(() => groupSessions(sessions, now, t), [sessions, now, t])
  const [deleting, setDeleting] = useState<SessionSummary | null>(null)
  const [deletePending, setDeletePending] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SessionSearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchFailed, setSearchFailed] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [confirmBatch, setConfirmBatch] = useState(false)
  const [batchPending, setBatchPending] = useState(false)
  const activeQuery = query.trim()

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const refresh = () => {
      clearTimeout(timer)
      const time = Date.now()
      setNow(time)
      const midnight = new Date(time).setHours(24, 0, 0, 0)
      timer = setTimeout(refresh, midnight - time)
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }
    refresh()
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('focus', refresh)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('focus', refresh)
    }
  }, [])

  useEffect(() => {
    if (!activeQuery) {
      setResults(null)
      setSearching(false)
      setSearchFailed(false)
      return
    }
    let cancelled = false
    setSearching(true)
    const timer = setTimeout(() => {
      void searchSessionList(activeQuery)
        .then((hits) => {
          if (cancelled) {
            return
          }
          setResults(hits)
          setSearchFailed(false)
        })
        .catch(() => {
          if (cancelled) {
            return
          }
          setResults([])
          setSearchFailed(true)
        })
        .finally(() => {
          if (!cancelled) {
            setSearching(false)
          }
        })
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeQuery])

  const clearSearch = () => setQuery('')

  const enterSelecting = () => {
    clearSearch()
    setSelectedIds(new Set())
    setSelecting(true)
  }

  const exitSelecting = () => {
    setSelecting(false)
    setSelectedIds(new Set())
  }

  const toggleSelected = (id: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const renderItem = (session: SessionSummary, extra?: Omit<SessionItemProps, 'session'>) => (
    <SessionItem
      key={session.id}
      session={session}
      active={session.id === activeId}
      onDelete={() => setDeleting(session)}
      selecting={selecting}
      selected={selectedIds.has(session.id)}
      onToggleSelect={() => toggleSelected(session.id)}
      {...extra}
    />
  )

  return (
    <div className={clsx(styles.sider, mobile && styles.mobile)}>
      <div className={styles.top}>
        <div className={styles.brand}>
          <BrandWordmark label={t('appName')} />
        </div>
        <IconButton
          size="m"
          icon={<IconPanelLeftOutline16 />}
          tooltip={t('collapseSiderbarButton')}
          tooltipPlacement="bottom"
          onClick={() => (mobile ? closeMobileSider() : setSiderCollapsed(true))}
        />
      </div>
      <button
        type="button"
        className={styles.newChat}
        onClick={() => {
          newChat()
          closeMobileSider()
        }}
      >
        <span className={styles.newChatIcon}>
          <NewChatIcon />
        </span>
        {t('newChat')}
      </button>
      <button
        type="button"
        className={clsx(styles.newChat, styles.newRoom)}
        onClick={() => {
          closeMobileSider()
          openNewRoom()
        }}
      >
        <span className={styles.newChatIcon}>
          <IconUsersOutline16 />
        </span>
        {t('newRoom')}
      </button>
      {selecting ? (
        <div className={styles.batchHeader}>
          <span className={styles.batchCount}>
            {t('sessionBatchSelected', { count: selectedIds.size })}
          </span>
          <button type="button" className={styles.batchCancel} onClick={exitSelecting}>
            {t('sessionBatchCancel')}
          </button>
        </div>
      ) : (
        <div className={styles.tools}>
          <div className={styles.searchBox}>
            <span className={styles.searchIcon}>
              <IconSearchOutline16 />
            </span>
            <input
              className={styles.searchInput}
              value={query}
              placeholder={t('searchSessionsPlaceholder')}
              aria-label={t('searchSessionsPlaceholder')}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  clearSearch()
                  event.currentTarget.blur()
                }
              }}
            />
            {query ? (
              <IconButton
                icon={<IconCloseOutline16 />}
                aria-label={t('searchSessionsClear')}
                onClick={clearSearch}
              />
            ) : null}
          </div>
          <IconButton
            size="m"
            icon={<IconCheckedOutline14 />}
            tooltip={t('sessionBatchManage')}
            onClick={enterSelecting}
          />
        </div>
      )}
      <div className={styles.listWrap}>
        <div className={styles.list}>
          {activeQuery ? (
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                {t('searchResult')}
                {results ? ` · ${results.length}` : ''}
              </div>
              {results === null ? (
                <div className={styles.placeholder}>{t('searchSessionsSearching')}</div>
              ) : searchFailed ? (
                <div className={styles.placeholder}>{t('searchSessionsFailed')}</div>
              ) : results.length === 0 ? (
                <div className={styles.placeholder}>{t('searchSessionsEmpty')}</div>
              ) : (
                results.map((hit) =>
                  renderItem(hit.session, {
                    snippet: hit.snippet,
                    matchCount: hit.matchCount,
                    onOpen: clearSearch,
                  })
                )
              )}
              {searching && results !== null && results.length > 0 ? (
                <div className={styles.placeholder}>{t('searchSessionsSearching')}</div>
              ) : null}
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.key} className={styles.section}>
                <div className={styles.sectionHeader}>{group.label}</div>
                {group.sessions.map((session) => renderItem(session))}
              </div>
            ))
          )}
        </div>
      </div>
      {selecting ? (
        <div className={styles.batchBar}>
          <Button
            variant="danger"
            size="m"
            block
            disabled={selectedIds.size === 0 || batchPending}
            onClick={() => setConfirmBatch(true)}
          >
            {t('sessionBatchDelete')}
          </Button>
        </div>
      ) : null}
      <div className={styles.bottom}>
        <button
          type="button"
          className={styles.settings}
          onClick={() => {
            closeMobileSider()
            openLore()
          }}
        >
          <span className={styles.settingsIcon}>
            <IconLibraryOutline16 />
          </span>
          <span className={styles.settingsLabel}>{t('lore')}</span>
        </button>
        <button type="button" className={styles.settings} onClick={() => openSettings('general')}>
          <span className={styles.settingsIcon}>
            <IconSettingsOutline16 />
          </span>
          <span className={styles.settingsLabel}>{t('settings')}</span>
        </button>
        <div className={styles.account}>
          <span className={styles.accountName}>{user?.name ?? ''}</span>
          <button
            type="button"
            className={styles.logout}
            onClick={() => {
              closeMobileSider()
              void useAuth.getState().logout()
            }}
          >
            {t('logout')}
          </button>
        </div>
      </div>
      {deleting ? (
        <Modal
          title={t('deleteSessionModalContentTitle')}
          onClose={() => {
            if (!deletePending) {
              setDeleting(null)
            }
          }}
          footer={
            <>
              <Button variant="outlined" disabled={deletePending} onClick={() => setDeleting(null)}>
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                disabled={deletePending}
                onClick={async () => {
                  setDeletePending(true)
                  try {
                    if (await removeSession(deleting.id)) {
                      setDeleting(null)
                    } else {
                      showToast({ key: 'sessionDeleteFailed', type: 'error' })
                    }
                  } finally {
                    setDeletePending(false)
                  }
                }}
              >
                {t('delete')}
              </Button>
            </>
          }
        >
          {t('deleteSessionWithFilesDescription')}
        </Modal>
      ) : null}
      {confirmBatch ? (
        <Modal
          title={t('sessionBatchDeleteTitle')}
          onClose={() => {
            if (!batchPending) {
              setConfirmBatch(false)
            }
          }}
          footer={
            <>
              <Button
                variant="outlined"
                disabled={batchPending}
                onClick={() => setConfirmBatch(false)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                disabled={batchPending}
                onClick={async () => {
                  setBatchPending(true)
                  try {
                    const count = await removeSessions([...selectedIds])
                    if (count > 0) {
                      showToast({
                        key: 'sessionBatchDeleteDone',
                        params: { count },
                      })
                      setConfirmBatch(false)
                      exitSelecting()
                    }
                  } finally {
                    setBatchPending(false)
                  }
                }}
              >
                {t('delete')}
              </Button>
            </>
          }
        >
          {t('sessionBatchDeleteDescription', { count: selectedIds.size })}
        </Modal>
      ) : null}
    </div>
  )
}

type SessionItemProps = {
  snippet?: string
  matchCount?: number
  onOpen?: () => void
}

/** 移动端抽屉：浮在页面上方，点空白区域关闭。 */
export const MobileSider = () => {
  const open = useMobileSider((state) => state.open)
  if (!open) {
    return null
  }
  return (
    <>
      <div className={styles.drawerMask} onClick={closeMobileSider} />
      <div className={styles.drawer}>
        <Sider mobile />
      </div>
    </>
  )
}