import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useAnchoredPosition, useDismiss } from '../../components/base/anchored.js'
import { IconButton } from '../../components/base/IconButton.js'
import { IconEllipsisOutline16 } from '../../components/base/icons.js'
import { showToast } from '../../components/base/toastState.js'
import { updateMember } from '../../services/api/rooms.js'
import { useChatStore } from '../../stores/chatStore.js'
import { useConfigStore } from '../../stores/configStore.js'
import { cardDisplayName, characterOf, useLoreStore } from '../../stores/loreStore.js'
import type { RoomMember, SessionDetail, Thread } from '../../types/session.js'
import { AddMemberDialog } from './AddMemberDialog.js'
import { GroupDialog } from './GroupDialog.js'
import { threadLabel } from './memberDisplay.js'
import { MembersDialog } from './MembersDialog.js'
import { ThreadDeleteDialog, ThreadRenameDialog } from './ThreadDialogs.js'
import styles from './RoomMenu.module.css'

type DialogKind = 'members' | 'add' | 'group' | 'rename' | 'delete' | null

const formatTokens = (value: number | undefined): string =>
  Number.isFinite(value ?? NaN) ? Number(value).toLocaleString('en-US') : '0'

/**
 * 会话 / 房间设置浮层：绑定世界观与角色、扮演自设、随机顺序，
 * 以及房间成员、群聊窗口、窗口重命名与删除（对照旧版 rooms.js 更多菜单）。
 * 内部下拉一律用原生 select：自定义 Select 的弹层挂在 body 上，
 * 父浮层的点外关闭会先于选项点击触发。
 */
export const RoomMenu = ({ detail, thread }: { detail: SessionDetail; thread: Thread | undefined }) => {
  const config = useConfigStore((state) => state.config)
  const ensureConfig = useConfigStore((state) => state.ensureLoaded)
  const characters = useLoreStore((state) => state.characters)
  const worlds = useLoreStore((state) => state.worlds)
  const ensureLore = useLoreStore((state) => state.ensureLoaded)
  const shuffle = useChatStore((state) => state.shuffle)
  const toggleShuffle = useChatStore((state) => state.toggleShuffle)
  const patchRoom = useChatStore((state) => state.patchRoom)
  const reloadAfterMutation = useChatStore((state) => state.reloadAfterMutation)

  const [open, setOpen] = useState(false)
  const [dialog, setDialog] = useState<DialogKind>(null)
  const [worldChoice, setWorldChoice] = useState<string | null>(null)
  const [characterChoice, setCharacterChoice] = useState<string | null>(null)
  const [cardChoice, setCardChoice] = useState<string | null>(null)
  const [personaChoice, setPersonaChoice] = useState<string | null>(null)

  const { anchorRef, floatingRef, position } = useAnchoredPosition<HTMLSpanElement, HTMLDivElement>(
    open,
    { side: 'bottom', align: 'end', offset: 6, maxHeight: 520 }
  )
  useDismiss(open, [anchorRef, floatingRef], () => setOpen(false))

  useEffect(() => {
    void ensureConfig()
    void ensureLore()
  }, [ensureConfig, ensureLore])

  // 会话重拉后丢弃本地等待态，下拉回到服务端真值
  useEffect(() => {
    setWorldChoice(null)
    setCharacterChoice(null)
    setCardChoice(null)
    setPersonaChoice(null)
  }, [detail])

  const isRoom = detail.threads.length > 0
  const isGalgame = detail.mode === 'galgame'
  const aiSeats = detail.members.filter((member) => !member.is_user)
  const userSeat = detail.members.find((member) => member.is_user) ?? null
  const singleAiSeat = isRoom && !isGalgame && aiSeats.length === 1 ? aiSeats[0] : null

  const providers = config?.providers ?? []
  const activeProvider =
    providers.find((provider) => provider.id === detail.provider_id) ??
    providers.find((provider) => provider.id === config?.active_provider) ??
    null
  const providerName = activeProvider
    ? activeProvider.model || activeProvider.id
    : '未配置提供商'

  const openDialog = (kind: DialogKind) => {
    setOpen(false)
    setDialog(kind)
  }

  const changeWorld = (value: string) => {
    setWorldChoice(value)
    void patchRoom({ world_id: value }).finally(() => setWorldChoice(null))
  }

  const changeCharacter = (value: string) => {
    setCharacterChoice(value)
    void patchRoom({ character_id: value }).finally(() => setCharacterChoice(null))
  }

  const changeSeatCharacter = async (seat: RoomMember, value: string) => {
    setCardChoice(value)
    try {
      await updateMember(detail.id, seat.id, { character_id: value })
      await reloadAfterMutation()
      const name = value ? cardDisplayName(characterOf(characters, value)) : '无角色'
      showToast(`已更换角色为「${name}」`)
    } catch (error) {
      showToast(`更换角色失败：${(error as Error).message}`, true)
    } finally {
      setCardChoice(null)
    }
  }

  const changePersona = async (seat: RoomMember, value: string) => {
    setPersonaChoice(value)
    try {
      await updateMember(detail.id, seat.id, { character_id: value })
      await reloadAfterMutation()
    } catch (error) {
      showToast(`设置自设失败：${(error as Error).message}`, true)
    } finally {
      setPersonaChoice(null)
    }
  }

  const worldValue =
    worldChoice ?? (detail.world_id && worlds.some((world) => world.id === detail.world_id)
      ? detail.world_id
      : '')
  const effectiveCharacterId = detail.scoped
    ? detail.character?.id ?? ''
    : detail.character_id || ''
  const characterValue =
    characterChoice ??
    (effectiveCharacterId && characters.some((card) => card.id === effectiveCharacterId)
      ? effectiveCharacterId
      : '')

  const cardValue =
    cardChoice ?? (singleAiSeat?.character_id ?? '')
  const personaValue = personaChoice ?? (userSeat?.character_id ?? '')

  return (
    <>
      <span ref={anchorRef} className={styles.anchor}>
        <IconButton
          size="m"
          icon={<IconEllipsisOutline16 />}
          tooltip="会话设置"
          onClick={() => setOpen((value) => !value)}
        />
      </span>

      {open
        ? createPortal(
            <div
              ref={floatingRef}
              className={styles.panel}
              style={position ?? undefined}
              role="dialog"
              aria-label="会话设置"
            >
              <div className={styles.info}>
                <div>◈ {providerName}</div>
                <div>🪙 累计 {formatTokens(detail.token_usage?.total_tokens)} tokens</div>
              </div>

              <div className={styles.sep} />

              <label className={styles.row}>
                <span className={styles.rowLabel}>🌍 世界观</span>
                <select
                  className={styles.select}
                  value={worldValue}
                  onChange={(event) => changeWorld(event.target.value)}
                >
                  <option value="">无世界观</option>
                  {worlds.map((world) => (
                    <option key={world.id} value={world.id}>
                      {world.name || '未命名'}
                    </option>
                  ))}
                </select>
              </label>

              {!isRoom ? (
                <label className={styles.row}>
                  <span className={styles.rowLabel}>👤 角色</span>
                  <select
                    className={styles.select}
                    value={characterValue}
                    onChange={(event) => changeCharacter(event.target.value)}
                  >
                    <option value="">无角色</option>
                    {characters.map((card) => (
                      <option key={card.id} value={card.id}>
                        {cardDisplayName(card)}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {isRoom ? (
                <>
                  <div className={styles.sep} />

                  {singleAiSeat ? (
                    <label className={styles.row}>
                      <span className={styles.rowLabel}>角色卡</span>
                      <select
                        className={styles.select}
                        value={cardValue}
                        onChange={(event) => void changeSeatCharacter(singleAiSeat, event.target.value)}
                      >
                        <option value="">
                          {singleAiSeat.override ? '会话副本（未入库）' : '无角色（自由对话）'}
                        </option>
                        {characters.map((card) => (
                          <option key={card.id} value={card.id}>
                            {cardDisplayName(card)}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}

                  {userSeat ? (
                    <label className={styles.row}>
                      <span className={styles.rowLabel}>🎭 扮演</span>
                      <select
                        className={styles.select}
                        value={personaValue}
                        onChange={(event) => void changePersona(userSeat, event.target.value)}
                      >
                        <option value="">本人</option>
                        {characters.map((card) => (
                          <option key={card.id} value={card.id}>
                            {cardDisplayName(card)}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}

                  <div className={styles.row}>
                    <span className={styles.rowLabel}>🔀 随机顺序</span>
                    <select
                      className={clsx(styles.select, styles.shuffleSelect)}
                      value={shuffle ? '1' : '0'}
                      onChange={() => toggleShuffle()}
                    >
                      <option value="0">关</option>
                      <option value="1">开</option>
                    </select>
                  </div>

                  <div className={styles.sep} />

                  <button
                    type="button"
                    className={styles.action}
                    onClick={() => openDialog('members')}
                  >
                    🧠 记忆与属性（座位 / 挂载 / 移除成员）
                  </button>
                  <button type="button" className={styles.action} onClick={() => openDialog('add')}>
                    ＋ 添加成员
                  </button>
                  <button
                    type="button"
                    className={styles.action}
                    disabled={aiSeats.length < 2}
                    title={aiSeats.length < 2 ? '至少需要两个 AI 参与者' : undefined}
                    onClick={() => openDialog('group')}
                  >
                    👥 建群聊
                  </button>
                  {thread ? (
                    <>
                      <button
                        type="button"
                        className={styles.action}
                        onClick={() => openDialog('rename')}
                      >
                        ✎ 重命名当前窗口「{threadLabel(thread)}」
                      </button>
                      <button
                        type="button"
                        className={clsx(styles.action, styles.actionDanger)}
                        disabled={detail.threads.length <= 1}
                        title={detail.threads.length <= 1 ? '最后一个窗口不能删除' : undefined}
                        onClick={() => openDialog('delete')}
                      >
                        🗑 删除当前窗口
                      </button>
                    </>
                  ) : null}
                </>
              ) : null}
            </div>,
            document.body
          )
        : null}

      {dialog === 'members' ? (
        <MembersDialog rid={detail.id} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'add' ? <AddMemberDialog rid={detail.id} onClose={() => setDialog(null)} /> : null}
      {dialog === 'group' ? (
        <GroupDialog
          rid={detail.id}
          members={aiSeats}
          onClose={() => setDialog(null)}
          onCreated={(tid) => {
            setDialog(null)
            useChatStore.getState().selectThread(tid)
          }}
        />
      ) : null}
      {dialog === 'rename' && thread ? (
        <ThreadRenameDialog
          rid={detail.id}
          thread={thread}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === 'delete' && thread ? (
        <ThreadDeleteDialog rid={detail.id} thread={thread} onClose={() => setDialog(null)} />
      ) : null}
    </>
  )
}