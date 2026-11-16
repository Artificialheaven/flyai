import { useState } from 'react'
import { Button } from '../../components/base/Button.js'
import { Modal } from '../../components/base/Modal.js'
import { showToast } from '../../components/base/toastState.js'
import { deleteThread, renameThread } from '../../services/api/rooms.js'
import { useChatStore } from '../../stores/chatStore.js'
import type { Thread } from '../../types/session.js'
import { threadLabel } from './memberDisplay.js'
import styles from './ThreadDialogs.module.css'

/** 重命名当前窗口（旧版只能改标题，输入为空会被后端拒绝，这里先本地校验） */
export const ThreadRenameDialog = ({
  rid,
  thread,
  onClose,
}: {
  rid: string
  thread: Thread
  onClose: () => void
}) => {
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)
  const [title, setTitle] = useState(thread.title || threadLabel(thread))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    const next = title.trim()
    if (!next) {
      setError('窗口名称不能为空')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await renameThread(rid, thread.id, next)
      await reloadAfterMutation()
      showToast('窗口已重命名')
      onClose()
    } catch (err) {
      setError((err as Error).message)
      setSaving(false)
    }
  }

  return (
    <Modal
      title="重命名窗口"
      closable
      className={styles.dialog}
      onClose={onClose}
      footer={
        <>
          <Button size="m" variant="outlined" onClick={onClose}>
            取消
          </Button>
          <Button size="m" disabled={saving} onClick={() => void submit()}>
            {saving ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <input
        autoFocus
        className={styles.input}
        value={title}
        maxLength={30}
        placeholder="窗口名称"
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            void submit()
          }
        }}
      />
      {error ? <div className={styles.error}>{error}</div> : null}
    </Modal>
  )
}

/** 删除当前窗口：消息随之删除且不可恢复；最后一个窗口后端不允许删 */
export const ThreadDeleteDialog = ({
  rid,
  thread,
  onClose,
}: {
  rid: string
  thread: Thread
  onClose: () => void
}) => {
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setDeleting(true)
    setError(null)
    try {
      await deleteThread(rid, thread.id)
      await reloadAfterMutation()
      showToast(`已删除窗口「${threadLabel(thread)}」`)
      onClose()
    } catch (err) {
      setError((err as Error).message)
      setDeleting(false)
    }
  }

  return (
    <Modal
      title="删除窗口"
      closable
      className={styles.dialog}
      onClose={onClose}
      footer={
        <>
          <Button size="m" variant="outlined" onClick={onClose}>
            取消
          </Button>
          <Button size="m" variant="danger" disabled={deleting} onClick={() => void submit()}>
            {deleting ? '删除中…' : '删除'}
          </Button>
        </>
      }
    >
      <p className={styles.text}>
        确定删除窗口「{threadLabel(thread)}」？窗口内全部消息将被删除，且无法恢复。
      </p>
      {error ? <div className={styles.error}>{error}</div> : null}
    </Modal>
  )
}