import clsx from 'clsx'
import { useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import {
  createGroupThread,
  deleteRoomThread,
  renameRoomThread,
  switchThread,
  useChatStore,
} from '../../services/chat/store.js'
import type { RoomMember } from '../../services/flyai/types.js'
import type { ThreadSummary } from '../../services/chat/types.js'
import { Button } from '../base/Button.js'
import { Menu } from '../base/Menu.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import {
  IconEditOutline16,
  IconEllipsisOutline16,
  IconPlusOutline16,
  IconTrashOutline16,
} from '../base/icons.js'
import styles from './ThreadTabs.module.css'

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** 房间窗口标签：私聊/群聊切换，支持新建群聊窗口、重命名与删除。 */
export const ThreadTabs = ({ sessionId }: { sessionId: string }) => {
  const t = useTranslate()
  const threads = useChatStore((state) => state.sessionCache[sessionId]?.threads)
  const members = useChatStore((state) => state.sessionCache[sessionId]?.members)
  const activeId = useChatStore((state) => state.sessionCache[sessionId]?.threadId)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<ThreadSummary | null>(null)
  const [renameText, setRenameText] = useState('')
  const [deleting, setDeleting] = useState<ThreadSummary | null>(null)
  const [busy, setBusy] = useState(false)

  const seats = useMemo(() => (members ?? []).filter((member) => !member.is_user), [members])
  const seatName = (id: string) =>
    members?.find((member) => member.id === id)?.name || t('roomSeat')

  // 单窗口房间不显示标签，加入第二个 AI 后自然出现
  if (!threads || threads.length <= 1) {
    return null
  }

  const label = (thread: ThreadSummary) => {
    if (thread.title) {
      return thread.title
    }
    if (thread.kind === 'dm') {
      return thread.memberIds.map(seatName).join('、') || t('roomDmTab')
    }
    return t('roomGroupTab')
  }

  const activate = async (thread: ThreadSummary) => {
    if (thread.id === activeId) {
      return
    }
    const ok = await switchThread(sessionId, thread.id)
    if (!ok) {
      showToast({ key: 'roomThreadSwitchBusy', type: 'error' })
    }
  }

  const doRename = async () => {
    if (!renaming || busy || !renameText.trim()) {
      return
    }
    setBusy(true)
    try {
      await renameRoomThread(sessionId, renaming.id, renameText)
      setRenaming(null)
    } catch (err) {
      showToast({ key: 'roomThreadRenameFailed', params: { message: errorText(err) }, type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async () => {
    if (!deleting || busy) {
      return
    }
    setBusy(true)
    try {
      await deleteRoomThread(sessionId, deleting.id)
      setDeleting(null)
    } catch (err) {
      showToast({ key: 'roomThreadDeleteFailed', params: { message: errorText(err) }, type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className={styles.tabs} role="tablist">
        {threads.map((thread) => (
          <div key={thread.id} className={clsx(styles.tab, thread.id === activeId && styles.active)}>
            <button
              type="button"
              role="tab"
              aria-selected={thread.id === activeId}
              className={styles.tabLabel}
              onClick={() => void activate(thread)}
            >
              {label(thread)}
            </button>
            <Menu
              options={[
                {
                  key: 'rename',
                  label: t('roomThreadRename'),
                  icon: <IconEditOutline16 />,
                  onSelect: () => {
                    setRenameText(label(thread))
                    setRenaming(thread)
                  },
                },
                {
                  key: 'delete',
                  label: t('delete'),
                  icon: <IconTrashOutline16 />,
                  danger: true,
                  onSelect: () => setDeleting(thread),
                },
              ]}
            >
              {({ open, toggle }) => (
                <button
                  type="button"
                  className={clsx(styles.tabMenu, open && styles.tabMenuOpen)}
                  aria-label={t('more')}
                  onClick={toggle}
                >
                  <IconEllipsisOutline16 />
                </button>
              )}
            </Menu>
          </div>
        ))}
        <button
          type="button"
          className={styles.add}
          aria-label={t('roomThreadNewGroup')}
          title={t('roomThreadNewGroup')}
          onClick={() => setCreating(true)}
        >
          <IconPlusOutline16 />
        </button>
      </div>

      {creating ? (
        <CreateGroupDialog
          sessionId={sessionId}
          seats={seats}
          onClose={() => setCreating(false)}
          onCreated={() => setCreating(false)}
        />
      ) : null}

      {renaming ? (
        <Modal
          title={t('roomThreadRename')}
          closable
          onClose={() => setRenaming(null)}
          footer={
            <>
              <Button variant="outlined" size="m" onClick={() => setRenaming(null)}>
                {t('cancel')}
              </Button>
              <Button size="m" disabled={busy || !renameText.trim()} onClick={() => void doRename()}>
                {t('loreSave')}
              </Button>
            </>
          }
        >
          <input
            className={styles.input}
            value={renameText}
            autoFocus
            onChange={(event) => setRenameText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                event.preventDefault()
                void doRename()
              }
            }}
          />
        </Modal>
      ) : null}

      {deleting ? (
        <Modal
          title={t('roomThreadDeleteTitle')}
          closable
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button variant="outlined" size="m" onClick={() => setDeleting(null)}>
                {t('cancel')}
              </Button>
              <Button variant="danger" size="m" disabled={busy} onClick={() => void doDelete()}>
                {t('delete')}
              </Button>
            </>
          }
        >
          <p>{t('roomThreadDeleteDesc', { title: label(deleting) })}</p>
        </Modal>
      ) : null}
    </>
  )
}

/** 新建群聊窗口：勾选参与本轮群聊的 AI 座位，可自定义窗口名。 */
const CreateGroupDialog = ({
  sessionId,
  seats,
  onClose,
  onCreated,
}: {
  sessionId: string
  seats: RoomMember[]
  onClose: () => void
  onCreated: () => void
}) => {
  const t = useTranslate()
  const [picked, setPicked] = useState<ReadonlySet<string>>(
    () => new Set(seats.map((seat) => seat.id))
  )
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (busy || picked.size === 0) {
      return
    }
    setBusy(true)
    try {
      const id = await createGroupThread(sessionId, [...picked], title.trim() || undefined)
      if (id) {
        onCreated()
      } else {
        showToast({ key: 'roomThreadCreateFailed', type: 'error' })
      }
    } catch (err) {
      showToast({
        key: 'roomThreadCreateFailedDetail',
        params: { message: errorText(err) },
        type: 'error',
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t('roomThreadNewGroup')}
      closable
      onClose={onClose}
      footer={
        <>
          <Button variant="outlined" size="m" onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy || picked.size === 0} onClick={() => void submit()}>
            {t('roomThreadCreate')}
          </Button>
        </>
      }
    >
      <div className={styles.field}>
        <div className={styles.fieldLabel}>{t('roomThreadTitle')}</div>
        <input
          className={styles.input}
          value={title}
          placeholder={t('roomThreadTitlePlaceholder')}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className={styles.field}>
        <div className={styles.fieldLabel}>{t('roomThreadMembers')}</div>
        <div className={styles.seatList}>
          {seats.map((seat) => {
            const checked = picked.has(seat.id)
            return (
              <button
                key={seat.id}
                type="button"
                className={clsx(styles.seat, checked && styles.seatOn)}
                aria-pressed={checked}
                onClick={() =>
                  setPicked((previous) => {
                    const next = new Set(previous)
                    if (next.has(seat.id)) {
                      next.delete(seat.id)
                    } else {
                      next.add(seat.id)
                    }
                    return next
                  })
                }
              >
                {seat.name || t('roomSeat')}
              </button>
            )
          })}
        </div>
        {seats.length === 0 ? (
          <div className={styles.hint}>{t('roomNoAiSeats')}</div>
        ) : null}
      </div>
    </Modal>
  )
}