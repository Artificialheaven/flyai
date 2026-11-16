import { useState } from 'react'
import { Avatar } from '../../components/base/Avatar.js'
import { Button } from '../../components/base/Button.js'
import { Modal } from '../../components/base/Modal.js'
import { showToast } from '../../components/base/toastState.js'
import { addMember } from '../../services/api/rooms.js'
import { useChatStore } from '../../stores/chatStore.js'
import { cardDisplayName, useLoreStore } from '../../stores/loreStore.js'
import styles from './AddMemberDialog.module.css'

/**
 * 添加房间成员（旧站无此入口，后端 POST /api/rooms/{rid}/members 一直可用）：
 * 每加一位 AI 自动多一条私聊窗口；房间正在回答时后端拒绝结构变更（409）。
 */
export const AddMemberDialog = ({ rid, onClose }: { rid: string; onClose: () => void }) => {
  const characters = useLoreStore((state) => state.characters)
  const detail = useChatStore((state) => state.detail)
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)

  const [selected, setSelected] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const seated = new Set(
    (detail?.members ?? [])
      .map((member) => member.character_id)
      .filter((id): id is string => Boolean(id))
  )
  const candidates = characters.filter((card) => !seated.has(card.id))

  const toggle = (id: string) => {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    )
  }

  const submit = async () => {
    if (selected.length === 0) {
      return
    }
    setAdding(true)
    setError(null)
    let added = 0
    try {
      for (const characterId of selected) {
        await addMember(rid, { character_id: characterId })
        added += 1
      }
      await reloadAfterMutation()
      showToast(`已添加 ${added} 位成员`)
      onClose()
    } catch (err) {
      setError((err as Error).message)
      setAdding(false)
      if (added > 0) {
        await reloadAfterMutation()
      }
    }
  }

  return (
    <Modal
      title="添加成员"
      closable
      className={styles.dialog}
      onClose={onClose}
      footer={
        <>
          <Button size="m" variant="outlined" onClick={onClose}>
            取消
          </Button>
          <Button
            size="m"
            disabled={selected.length === 0 || adding}
            onClick={() => void submit()}
          >
            {adding ? '添加中…' : `添加${selected.length ? `（${selected.length}）` : ''}`}
          </Button>
        </>
      }
    >
      <p className={styles.subtitle}>从角色库挑选 AI 参与者，每位移入后自动建立一条私聊窗口。</p>
      {candidates.length === 0 ? (
        <div className={styles.empty}>角色库里的卡都已在本房间；可先到角色库新建或派生角色。</div>
      ) : (
        <div className={styles.list}>
          {candidates.map((card) => {
            const checked = selected.includes(card.id)
            return (
              <label key={card.id} className={checked ? styles.rowActive : styles.row}>
                <input
                  type="checkbox"
                  className={styles.check}
                  checked={checked}
                  onChange={() => toggle(card.id)}
                />
                <Avatar src={card.avatar || null} name={cardDisplayName(card)} size={28} />
                <span className={styles.name}>{cardDisplayName(card)}</span>
                {card.intro ? <span className={styles.intro}>{card.intro}</span> : null}
              </label>
            )
          })}
        </div>
      )}
      {error ? <div className={styles.error}>{error}</div> : null}
    </Modal>
  )
}