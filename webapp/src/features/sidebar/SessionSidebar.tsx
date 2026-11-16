import clsx from 'clsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '../../components/base/Button.js'
import { IconButton } from '../../components/base/IconButton.js'
import { Menu, type MenuOption } from '../../components/base/Menu.js'
import { Modal } from '../../components/base/Modal.js'
import {
  IconCheckedOutline14,
  IconDataOutline16,
  IconEditOutline16,
  IconEllipsisOutline16,
  IconNewChatOutline20,
  IconSearchOutline16,
  IconTrashOutline16,
} from '../../components/base/icons.js'
import { navigate } from '../../app/router.js'
import { NewRoomDialog } from '../rooms/NewRoomDialog.js'
import { useAuth } from '../../stores/auth.js'
import { useChatStore } from '../../stores/chatStore.js'
import { filterSessions, useSessionStore } from '../../stores/sessionStore.js'
import { ThemeSelect } from '../../app/ThemeSelect.js'
import type { SessionSummary } from '../../types/session.js'
import styles from './SessionSidebar.module.css'

const LONG_PRESS_MS = 500

/**
 * 会话侧栏：搜索（前端标题过滤）、新建、重命名、置顶、删除、批量管理。
 * 触屏长按会话项直接进入重命名（沿用旧站交互）。
 */
export const SessionSidebar = ({ onNavigate }: { onNavigate?: () => void }) => {
  const sessions = useSessionStore((state) => state.sessions)
  const loading = useSessionStore((state) => state.loading)
  const keyword = useSessionStore((state) => state.keyword)
  const batchMode = useSessionStore((state) => state.batchMode)
  const batchIds = useSessionStore((state) => state.batchIds)
  const refresh = useSessionStore((state) => state.refresh)
  const rename = useSessionStore((state) => state.rename)
  const setPinned = useSessionStore((state) => state.setPinned)
  const remove = useSessionStore((state) => state.remove)
  const batchRemove = useSessionStore((state) => state.batchRemove)
  const setKeyword = useSessionStore((state) => state.setKeyword)
  const toggleBatchMode = useSessionStore((state) => state.toggleBatchMode)
  const toggleBatchId = useSessionStore((state) => state.toggleBatchId)

  const activeSid = useChatStore((state) => state.sid)
  const openSession = useChatStore((state) => state.openSession)
  const resetChat = useChatStore((state) => state.reset)
  const user = useAuth((state) => state.user)
  const logout = useAuth((state) => state.logout)

  const [renameTarget, setRenameTarget] = useState<SessionSummary | null>(null)
  const [renameText, setRenameText] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null)
  const [newRoomOpen, setNewRoomOpen] = useState(false)

  useEffect(() => {
    void refresh()
    // 仅挂载时拉取一次；后续增删改由 store 本地维护，终态重拉由 chatStore 回写
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const visible = useMemo(() => filterSessions(sessions, keyword), [sessions, keyword])

  const handleOpen = async (session: SessionSummary) => {
    await openSession(session.id)
    navigate('/')
    onNavigate?.()
  }

  const handleOpenLore = () => {
    navigate('/lore')
    onNavigate?.()
  }

  const handleCreate = () => {
    setNewRoomOpen(true)
  }

  const handleDelete = async () => {
    if (!deleteTarget) {
      return
    }
    const target = deleteTarget
    setDeleteTarget(null)
    if (activeSid === target.id) {
      resetChat()
    }
    await remove(target.id)
  }

  const handleBatchRemove = async () => {
    if (activeSid && batchIds.includes(activeSid)) {
      resetChat()
    }
    await batchRemove()
  }

  const openRename = (session: SessionSummary) => {
    setRenameTarget(session)
    setRenameText(session.title)
  }

  const submitRename = () => {
    if (renameTarget && renameText.trim()) {
      void rename(renameTarget.id, renameText)
    }
    setRenameTarget(null)
  }

  return (
    <aside className={styles.sidebar}>
      <div className={styles.header}>
        <span className={styles.brand}>FlyAI</span>
        <div className={styles.headerActions}>
          <IconButton
            size="m"
            icon={<IconNewChatOutline20 />}
            tooltip="新建对话"
            onClick={handleCreate}
          />
          <IconButton
            size="m"
            icon={<IconDataOutline16 />}
            tooltip="内容库（角色卡 / 世界观）"
            onClick={handleOpenLore}
          />
          <button
            type="button"
            className={clsx(styles.batchToggle, batchMode && styles.batchToggleActive)}
            onClick={() => toggleBatchMode()}
          >
            {batchMode ? '完成' : '批量'}
          </button>
        </div>
      </div>

      <div className={styles.searchBox}>
        <span className={styles.searchIcon}>
          <IconSearchOutline16 />
        </span>
        <input
          className={styles.searchInput}
          placeholder="搜索会话标题"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
      </div>

      {batchMode ? (
        <div className={styles.batchBar}>
          <span className={styles.batchCount}>已选 {batchIds.length} 项</span>
          <div className={styles.batchButtons}>
            <Button size="s" variant="outlined" onClick={() => toggleBatchMode(false)}>
              取消
            </Button>
            <Button
              size="s"
              variant="danger"
              disabled={batchIds.length === 0}
              onClick={() => void handleBatchRemove()}
            >
              删除
            </Button>
          </div>
        </div>
      ) : null}

      <div className={styles.list}>
        {visible.length === 0 ? (
          <div className={styles.empty}>
            <div>{loading ? '加载中…' : keyword ? '没有匹配的会话' : '暂无会话'}</div>
            {!loading && !keyword ? <div className={styles.emptyAction}>{'点击上方 ＋ 新建对话'}</div> : null}
          </div>
        ) : null}
        {visible.map((session) => (
          <SessionRow
            key={session.id}
            session={session}
            active={session.id === activeSid}
            batchMode={batchMode}
            checked={batchIds.includes(session.id)}
            onOpen={() => void handleOpen(session)}
            onToggleCheck={() => toggleBatchId(session.id)}
            onLongPress={() => openRename(session)}
            options={[
              {
                key: 'rename',
                label: '重命名',
                icon: <IconEditOutline16 />,
                onSelect: () => openRename(session),
              },
              {
                key: 'pin',
                label: session.pinned ? '取消置顶' : '置顶',
                icon: <IconEllipsisOutline16 />,
                onSelect: () => void setPinned(session.id, !session.pinned),
              },
              {
                key: 'delete',
                label: '删除',
                icon: <IconTrashOutline16 />,
                danger: true,
                onSelect: () => setDeleteTarget(session),
              },
            ]}
          />
        ))}
      </div>

      <div className={styles.footer}>
        <div className={styles.userChip}>
          <span className={styles.userName}>{user?.name ?? '未登录'}</span>
          <span className={styles.userMeta}>
            {user?.role === 'admin' ? '管理员' : '普通用户'} · Lv{user?.level ?? 0}
          </span>
        </div>
        <ThemeSelect className={styles.themeSelect} />
        <Button size="s" variant="outlined" onClick={() => void logout()}>
          退出
        </Button>
      </div>

      {renameTarget ? (
        <Modal
          title="重命名会话"
          closable
          onClose={() => setRenameTarget(null)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setRenameTarget(null)}>
                取消
              </Button>
              <Button size="m" disabled={!renameText.trim()} onClick={submitRename}>
                保存
              </Button>
            </>
          }
        >
          <input
            // 弹窗挂载即聚焦，回车确认
            autoFocus
            className={styles.renameInput}
            value={renameText}
            onChange={(event) => setRenameText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                submitRename()
              }
            }}
          />
        </Modal>
      ) : null}

      {deleteTarget ? (
        <Modal
          title="删除会话"
          closable
          onClose={() => setDeleteTarget(null)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setDeleteTarget(null)}>
                取消
              </Button>
              <Button size="m" variant="danger" onClick={() => void handleDelete()}>
                删除
              </Button>
            </>
          }
        >
          <p className={styles.confirmText}>
            确定删除会话「{deleteTarget.title}」吗？该操作不可恢复。
          </p>
        </Modal>
      ) : null}

      {newRoomOpen ? (
        <NewRoomDialog
          onClose={() => setNewRoomOpen(false)}
          onCreated={() => {
            navigate('/')
            onNavigate?.()
          }}
        />
      ) : null}
    </aside>
  )
}

const SessionRow = ({
  session,
  active,
  batchMode,
  checked,
  onOpen,
  onToggleCheck,
  onLongPress,
  options,
}: {
  session: SessionSummary
  active: boolean
  batchMode: boolean
  checked: boolean
  onOpen: () => void
  onToggleCheck: () => void
  onLongPress: () => void
  options: MenuOption[]
}) => {
  const pressTimer = useRef<number | null>(null)
  const longPressed = useRef(false)

  const clearTimer = () => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current)
      pressTimer.current = null
    }
  }

  const handlePointerDown = (event: React.PointerEvent) => {
    if (event.pointerType !== 'touch' || batchMode) {
      return
    }
    longPressed.current = false
    pressTimer.current = window.setTimeout(() => {
      longPressed.current = true
      onLongPress()
    }, LONG_PRESS_MS)
  }

  const handleClick = () => {
    if (longPressed.current) {
      longPressed.current = false
      return
    }
    if (batchMode) {
      onToggleCheck()
    } else {
      onOpen()
    }
  }

  return (
    <div
      className={clsx(styles.row, active && styles.rowActive)}
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onPointerDown={handlePointerDown}
      onPointerUp={clearTimer}
      onPointerLeave={clearTimer}
      onPointerCancel={clearTimer}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          handleClick()
        }
      }}
    >
      {batchMode ? (
        <span className={clsx(styles.checkbox, checked && styles.checkboxChecked)}>
          {checked ? <IconCheckedOutline14 /> : null}
        </span>
      ) : null}
      <div className={styles.rowBody}>
        <div className={styles.rowTitleLine}>
          {session.pinned ? <span className={styles.pinTag}>置顶</span> : null}
          {session.mode === 'galgame' ? <span className={styles.galgameTag}>Galgame</span> : null}
          <span className={styles.rowTitle}>{session.title || '未命名会话'}</span>
        </div>
      </div>
      {!batchMode ? (
        <Menu options={options}>
          {({ toggle }) => (
            <IconButton
              size="xs"
              icon={<IconEllipsisOutline16 />}
              tooltip="会话操作"
              onClick={(event) => {
                event.stopPropagation()
                toggle()
              }}
            />
          )}
        </Menu>
      ) : null}
    </div>
  )
}
