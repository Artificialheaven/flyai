import { useState } from 'react'
import { Avatar } from '../../components/base/Avatar.js'
import { Button } from '../../components/base/Button.js'
import { Modal } from '../../components/base/Modal.js'
import { showToast } from '../../components/base/toastState.js'
import { publishMemberCharacter, removeMember, updateMember } from '../../services/api/rooms.js'
import { useChatStore } from '../../stores/chatStore.js'
import { providerLabel, useConfigStore } from '../../stores/configStore.js'
import { cardDisplayName, useLoreStore } from '../../stores/loreStore.js'
import type { RoomMember } from '../../types/session.js'
import { memberAvatar, memberDisplayName, selectValue } from './memberDisplay.js'
import styles from './MembersDialog.module.css'

const stringifyAttributes = (value: Record<string, unknown> | undefined): string => {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

/**
 * 房间成员记忆与属性（对照旧版 openMemDialog）：
 * 每个 AI 座位可单独指定提供商、挂载跨会话记忆簿、编辑本房间记忆与属性，
 * 会话副本可入库，成员可移出房间。普通聊天沿用同一会话，不在此列。
 */
export const MembersDialog = ({ rid, onClose }: { rid: string; onClose: () => void }) => {
  const detail = useChatStore((state) => state.detail)
  const seats = detail?.members.filter((member) => !member.is_user) ?? []

  return (
    <Modal
      title="记忆与属性"
      closable
      className={styles.dialog}
      mainClassName={styles.main}
      onClose={onClose}
      footer={
        <Button size="m" variant="outlined" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <p className={styles.subtitle}>
        房间号 {rid.slice(0, 8)} · 各参与者在本房间的记忆（各窗口共享）与属性
      </p>
      {seats.length === 0 ? (
        <div className={styles.empty}>房间内还没有 AI 参与者。</div>
      ) : (
        seats.map((seat) => <MemberBlock key={seat.id} rid={rid} member={seat} onRemove={onClose} />)
      )}
    </Modal>
  )
}

const MemberBlock = ({
  rid,
  member,
  onRemove,
}: {
  rid: string
  member: RoomMember
  onRemove: () => void
}) => {
  const characters = useLoreStore((state) => state.characters)
  const refreshLore = useLoreStore((state) => state.refresh)
  const providers = useConfigStore((state) => state.config?.providers ?? [])
  const applyMember = useChatStore((state) => state.applyMember)
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)

  const [providerId, setProviderId] = useState(member.provider_id ?? '')
  const [memoryMode, setMemoryMode] = useState(member.memory_mode ?? '')
  const [memoryCharId, setMemoryCharId] = useState(member.memory_char_id ?? '')
  const [memory, setMemory] = useState(member.memory ?? '')
  const [attributesText, setAttributesText] = useState(() => stringifyAttributes(member.attributes))
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [confirmPublish, setConfirmPublish] = useState(false)
  const [busyRemoving, setBusyRemoving] = useState(false)

  const characterIds = characters.map((card) => card.id)

  const save = async () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(attributesText || '{}')
    } catch {
      setMessage({ text: '属性 JSON 解析失败', error: true })
      return
    }
    if (Array.isArray(parsed)) {
      setMessage({ text: '属性请提交对象，而非数组', error: true })
      return
    }
    if (typeof parsed !== 'object' || parsed === null) {
      setMessage({ text: '属性请提交对象，而非数组', error: true })
      return
    }
    setSaving(true)
    setMessage(null)
    try {
      const updated = await updateMember(rid, member.id, {
        memory,
        attributes: parsed as Record<string, unknown>,
        provider_id: providerId,
        memory_mode: memoryMode as '' | 'rw' | 'ro',
        memory_char_id: memoryCharId,
      })
      applyMember(updated)
      setMessage({ text: '已保存' })
    } catch (error) {
      setMessage({ text: (error as Error).message, error: true })
    } finally {
      setSaving(false)
    }
  }

  const publish = async () => {
    setConfirmPublish(false)
    setPublishing(true)
    try {
      const card = await publishMemberCharacter(rid, member.id)
      showToast(`已保存为角色卡：${card.name || ''}`)
      await refreshLore()
    } catch (error) {
      showToast(`入库失败：${(error as Error).message}`, true)
    } finally {
      setPublishing(false)
    }
  }

  const remove = async () => {
    setConfirmRemove(false)
    setBusyRemoving(true)
    try {
      await removeMember(rid, member.id)
      await reloadAfterMutation()
      onRemove()
      showToast(`已移除成员「${memberDisplayName(member, characters)}」`)
    } catch (error) {
      showToast(`移除失败：${(error as Error).message}`, true)
      setBusyRemoving(false)
    }
  }

  return (
    <div className={styles.block}>
      <div className={styles.head}>
        <Avatar src={memberAvatar(member, characters)} name={memberDisplayName(member, characters)} size={30} />
        <span className={styles.name}>{memberDisplayName(member, characters)}</span>
        {member.override ? <span className={styles.tag}>会话副本</span> : null}
      </div>

      <div className={styles.seatLine}>
        <select
          className={styles.select}
          title="该座位专属 LLM；不选则用房间默认提供商"
          value={selectValue(providerId, providers.map((provider) => provider.id))}
          onChange={(event) => setProviderId(event.target.value)}
        >
          <option value="">房间默认提供商</option>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {providerLabel(provider)}
            </option>
          ))}
        </select>

        <select
          className={styles.select}
          title="跨会话角色记忆簿的挂载方式"
          value={memoryMode}
          onChange={(event) => setMemoryMode(event.target.value as '' | 'rw' | 'ro')}
        >
          <option value="">不挂记忆簿</option>
          <option value="rw">记忆簿·读写</option>
          <option value="ro">记忆簿·只读</option>
        </select>

        <select
          className={styles.select}
          title="挂载/写入的记忆簿归属角色；自己的角色无独立记忆簿时可挂别的卡"
          value={selectValue(memoryCharId, characterIds)}
          onChange={(event) => setMemoryCharId(event.target.value)}
        >
          <option value="">本角色的记忆簿</option>
          {characters.map((card) => (
            <option key={card.id} value={card.id}>
              {cardDisplayName(card)}（{card.id.slice(0, 6)}）
            </option>
          ))}
        </select>

        {member.override ? (
          <Button
            size="s"
            variant="outlined"
            disabled={publishing}
            onClick={() => setConfirmPublish(true)}
          >
            {publishing ? '入库中…' : '💾 副本入库'}
          </Button>
        ) : null}
        <Button
          size="s"
          variant="dangerOutlined"
          disabled={busyRemoving}
          onClick={() => setConfirmRemove(true)}
        >
          移除成员
        </Button>
      </div>

      <textarea
        className={styles.textarea}
        rows={2}
        placeholder="该 AI 在本房间的记忆（各窗口共享）"
        value={memory}
        onChange={(event) => setMemory(event.target.value)}
      />
      <textarea
        className={`${styles.textarea} ${styles.attrs}`}
        rows={2}
        placeholder='属性 JSON，如 {"魅力":80,"好感度":50}'
        value={attributesText}
        onChange={(event) => setAttributesText(event.target.value)}
      />
      <div className={styles.saveLine}>
        <Button size="s" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : '保存'}
        </Button>
        {message ? (
          <span className={message.error ? styles.error : styles.ok}>{message.text}</span>
        ) : null}
      </div>

      {confirmRemove ? (
        <Modal
          title="移除房间成员"
          closable
          onClose={() => setConfirmRemove(false)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setConfirmRemove(false)}>
                取消
              </Button>
              <Button size="m" variant="danger" onClick={() => void remove()}>
                移除
              </Button>
            </>
          }
        >
          <p>
            确定把「{memberDisplayName(member, characters)}」移出房间？其私聊窗口将删除，
            各群聊窗口不再有它（历史消息保留）。
          </p>
        </Modal>
      ) : null}

      {confirmPublish ? (
        <Modal
          title="座位副本入库"
          closable
          onClose={() => setConfirmPublish(false)}
          footer={
            <>
              <Button size="m" variant="outlined" onClick={() => setConfirmPublish(false)}>
                取消
              </Button>
              <Button size="m" onClick={() => void publish()}>
                入库
              </Button>
            </>
          }
        >
          <p>
            把「{memberDisplayName(member, characters)}」当前的会话副本另存为角色库中的一张新卡？
            房间里仍继续使用这个副本，库主卡不受影响。重复操作会再生成一张新卡。
          </p>
        </Modal>
      ) : null}
    </div>
  )
}