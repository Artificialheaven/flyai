import { useEffect, useMemo, useState } from 'react'
import { Avatar } from '../../components/base/Avatar.js'
import { Button } from '../../components/base/Button.js'
import { Modal } from '../../components/base/Modal.js'
import { IconChevronRightOutline14, IconCloseFill14 } from '../../components/base/icons.js'
import { showToast } from '../../components/base/toastState.js'
import { createSession } from '../../services/api/sessions.js'
import { createRoom } from '../../services/api/rooms.js'
import { useAuth } from '../../stores/auth.js'
import { useChatStore } from '../../stores/chatStore.js'
import {
  cardDisplayName,
  isOwnCard,
  rootCards,
  useLoreStore,
  variantCards,
} from '../../stores/loreStore.js'
import { useSessionStore } from '../../stores/sessionStore.js'
import styles from './NewRoomDialog.module.css'

/**
 * 新建对话：根角色卡网格 → 主卡/副本多选（可跨根卡）→ 建房间（每个 AI 一条私聊）。
 * 「直接使用通用助手」保留旧版普通入口（不绑定角色）。
 */
export const NewRoomDialog = ({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated?: () => void
}) => {
  const characters = useLoreStore((state) => state.characters)
  const loading = useLoreStore((state) => state.loading)
  const ensureLoaded = useLoreStore((state) => state.ensureLoaded)
  const user = useAuth((state) => state.user)
  const openSession = useChatStore((state) => state.openSession)
  const refreshSessions = useSessionStore((state) => state.refresh)

  const [title, setTitle] = useState('')
  const [rootId, setRootId] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    void ensureLoaded()
  }, [ensureLoaded])

  const roots = useMemo(() => rootCards(characters), [characters])
  const activeRoot = rootId ? characters.find((card) => card.id === rootId) ?? null : null
  const variants = useMemo(
    () => (rootId ? variantCards(characters, rootId) : []),
    [characters, rootId]
  )

  const togglePick = (id: string) => {
    setPicked((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    )
  }

  const pickedCards = picked
    .map((id) => characters.find((card) => card.id === id))
    .filter((card): card is NonNullable<typeof card> => Boolean(card))

  const finish = async (sid: string) => {
    await refreshSessions()
    onClose()
    await openSession(sid)
    onCreated?.()
  }

  const submitRoom = async () => {
    if (picked.length === 0 || creating) {
      return
    }
    setCreating(true)
    try {
      const room = await createRoom({
        ...(title.trim() ? { title: title.trim() } : {}),
        character_ids: picked,
      })
      await finish(room.id)
    } catch (error) {
      showToast(`创建房间失败：${(error as Error).message}`, true)
      setCreating(false)
    }
  }

  const submitPlain = async () => {
    if (creating) {
      return
    }
    setCreating(true)
    try {
      const session = await createSession(title.trim())
      await finish(session.id)
    } catch (error) {
      showToast(`创建会话失败：${(error as Error).message}`, true)
      setCreating(false)
    }
  }

  return (
    <Modal
      title="新建对话"
      closable
      className={styles.dialog}
      mainClassName={styles.main}
      onClose={() => {
        if (!creating) {
          onClose()
        }
      }}
      footer={
        <>
          <Button size="m" disabled={creating || picked.length === 0} onClick={() => void submitRoom()}>
            创建房间{picked.length > 0 ? `（${picked.length}）` : ''}
          </Button>
          <Button size="m" variant="outlined" disabled={creating} onClick={() => void submitPlain()}>
            直接使用通用助手
          </Button>
        </>
      }
    >
      <label className={styles.titleRow}>
        <span className={styles.titleLabel}>会话名称</span>
        <input
          className={styles.titleInput}
          value={title}
          maxLength={60}
          placeholder="留空则自动命名"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>

      <div className={styles.navBar}>
        {rootId ? (
          <button type="button" className={styles.back} onClick={() => setRootId(null)}>
            ← 返回根角色卡
          </button>
        ) : null}
        <span className={styles.hint}>
          {activeRoot
            ? `选择「${cardDisplayName(activeRoot)}」的主卡或副本`
            : '选一张角色卡，或直接使用通用助手'}
        </span>
      </div>

      {!rootId ? (
        loading && roots.length === 0 ? (
          <div className={styles.empty}>内容库加载中…</div>
        ) : roots.length === 0 ? (
          <div className={styles.empty}>角色库还没有角色卡，请先在「内容库」里创建。</div>
        ) : (
          <div className={styles.rootGrid}>
            {roots.map((card) => {
              const count = variantCards(characters, card.id).length
              const own = isOwnCard(card, user?.id ?? null, user?.role === 'admin')
              return (
                <button
                  key={card.id}
                  type="button"
                  className={styles.rootCard}
                  onClick={() => setRootId(card.id)}
                >
                  <Avatar src={card.avatar} name={card.name} size={48} />
                  <span className={styles.rootName}>{card.name || '未命名'}</span>
                  <span className={styles.rootMeta}>
                    {own ? '' : '🌐 '}
                    {count} 个副本
                  </span>
                </button>
              )
            })}
          </div>
        )
      ) : (
        <div className={styles.variantList}>
          {activeRoot ? (
            <VariantRow
              card={activeRoot}
              isRoot
              picked={picked.includes(activeRoot.id)}
              onToggle={() => togglePick(activeRoot.id)}
            />
          ) : null}
          {variants.map((card) => (
            <VariantRow
              key={card.id}
              card={card}
              picked={picked.includes(card.id)}
              onToggle={() => togglePick(card.id)}
            />
          ))}
          {variants.length === 0 ? <div className={styles.emptySub}>暂无副本</div> : null}
        </div>
      )}

      {pickedCards.length > 0 ? (
        <div className={styles.tray}>
          <span className={styles.trayLabel}>已选：</span>
          {pickedCards.map((card) => (
            <span key={card.id} className={styles.chip}>
              {cardDisplayName(card)}
              <button
                type="button"
                className={styles.chipX}
                title="移除"
                onClick={() => togglePick(card.id)}
              >
                <IconCloseFill14 />
              </button>
            </span>
          ))}
        </div>
      ) : null}
    </Modal>
  )
}

const VariantRow = ({
  card,
  isRoot = false,
  picked,
  onToggle,
}: {
  card: { avatar: string; name: string; alias?: string }
  isRoot?: boolean
  picked: boolean
  onToggle: () => void
}) => (
  <button
    type="button"
    className={`${styles.variantRow} ${picked ? styles.variantPicked : ''}`}
    onClick={onToggle}
  >
    <span className={`${styles.tag} ${isRoot ? '' : styles.tagVariant}`}>{isRoot ? '主卡' : '副本'}</span>
    <Avatar src={card.avatar} name={card.name} size={34} />
    <span className={styles.variantName}>{card.alias || card.name || '未命名'}</span>
    {picked ? <span className={styles.check}>✓</span> : <IconChevronRightOutline14 />}
  </button>
)