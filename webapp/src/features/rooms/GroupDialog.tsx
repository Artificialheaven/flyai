import { useState } from 'react'
import { Avatar } from '../../components/base/Avatar.js'
import { Button } from '../../components/base/Button.js'
import { Modal } from '../../components/base/Modal.js'
import { createGroupThread } from '../../services/api/rooms.js'
import { useChatStore } from '../../stores/chatStore.js'
import { useLoreStore } from '../../stores/loreStore.js'
import type { RoomMember } from '../../types/session.js'
import { memberAvatar, memberDisplayName } from './memberDisplay.js'
import styles from './GroupDialog.module.css'

/**
 * 建群聊窗口（对照旧版 openGroupDialog）：勾选至少两位 AI，
 * 群聊内按窗口顺序或随机顺序轮流发言，输入框可用 @ 点名指定先答者。
 */
export const GroupDialog = ({
  rid,
  members,
  onClose,
  onCreated,
}: {
  rid: string
  members: RoomMember[]
  onClose: () => void
  onCreated: (tid: string) => void
}) => {
  const characters = useLoreStore((state) => state.characters)
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)

  const [selected, setSelected] = useState<string[]>(members.map((member) => member.id))
  const [title, setTitle] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = (id: string) => {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    )
  }

  const submit = async () => {
    if (selected.length < 2) {
      setError('至少选择两个 AI 参与者')
      return
    }
    setCreating(true)
    setError(null)
    try {
      const thread = await createGroupThread(rid, {
        member_ids: selected,
        title: title.trim() || undefined,
      })
      await reloadAfterMutation()
      onCreated(thread.id)
    } catch (err) {
      setError((err as Error).message)
      setCreating(false)
    }
  }

  return (
    <Modal
      title="建群聊"
      closable
      className={styles.dialog}
      onClose={onClose}
      footer={
        <>
          <Button size="m" variant="outlined" onClick={onClose}>
            取消
          </Button>
          <Button size="m" disabled={creating} onClick={() => void submit()}>
            {creating ? '创建中…' : '建群'}
          </Button>
        </>
      }
    >
      <p className={styles.subtitle}>
        勾选参与本群聊的 AI 参与者（至少两位）。群内默认按顺序轮流发言，可在输入框用 @ 点名。
      </p>
      <div className={styles.list}>
        {members.map((member) => {
          const checked = selected.includes(member.id)
          return (
            <label key={member.id} className={checked ? styles.rowActive : styles.row}>
              <input
                type="checkbox"
                className={styles.check}
                checked={checked}
                onChange={() => toggle(member.id)}
              />
              <Avatar
                src={memberAvatar(member, characters)}
                name={memberDisplayName(member, characters)}
                size={28}
              />
              <span className={styles.name}>{memberDisplayName(member, characters)}</span>
            </label>
          )
        })}
      </div>
      <input
        className={styles.titleInput}
        placeholder="群聊名称（留空自动生成）"
        value={title}
        maxLength={30}
        onChange={(event) => setTitle(event.target.value)}
      />
      {error ? <div className={styles.error}>{error}</div> : null}
    </Modal>
  )
}