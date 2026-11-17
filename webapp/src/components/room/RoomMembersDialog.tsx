import clsx from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { assetUrl } from '../../services/flyai/client.js'
import { listCharacters } from '../../services/flyai/lore.js'
import type { Character, RoomMember } from '../../services/flyai/types.js'
import {
  addRoomMember,
  publishRoomMember,
  removeRoomMember,
  updateRoomMember,
  useChatStore,
} from '../../services/chat/store.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Select } from '../base/Select.js'
import { showToast } from '../base/toastState.js'
import {
  IconEditOutline16,
  IconPlusOutline16,
  IconTrashOutline16,
} from '../base/icons.js'
import styles from './RoomMembersDialog.module.css'

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** 房间座位管理：增删 AI、换角色、记忆与属性、把专属副本另存为新卡。 */
export const RoomMembersDialog = ({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) => {
  const t = useTranslate()
  const session = useChatStore((state) => state.sessionCache[sessionId])
  const [characters, setCharacters] = useState<Character[]>([])
  const [loadError, setLoadError] = useState('')
  const [busy, setBusy] = useState('')
  const [editing, setEditing] = useState('')
  const [memoryDraft, setMemoryDraft] = useState('')
  const [attrsDraft, setAttrsDraft] = useState('')
  const [attrsError, setAttrsError] = useState('')
  const [addId, setAddId] = useState('')
  const [removing, setRemoving] = useState<RoomMember | null>(null)

  useEffect(() => {
    let cancelled = false
    void listCharacters()
      .then((chars) => {
        if (!cancelled) {
          setCharacters(chars)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(errorText(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const charById = useMemo(
    () => new Map(characters.map((character) => [character.id, character])),
    [characters]
  )
  const seats = session?.members ?? []
  const aiSeats = seats.filter((member) => !member.is_user)
  const userSeat = seats.find((member) => member.is_user)

  const characterOptions = characters.map((character) => ({
    value: character.id,
    label: character.name || character.alias || character.id,
  }))

  const run = async (key: string, action: () => Promise<void>, done?: string) => {
    if (busy) {
      return
    }
    setBusy(key)
    setLoadError('')
    try {
      await action()
      if (done) {
        showToast(done)
      }
    } catch (err) {
      setLoadError(errorText(err))
    } finally {
      setBusy('')
    }
  }

  const avatarOf = (member: RoomMember) => {
    const chair = charById.get(member.character_id)
    return member.override?.avatar || chair?.avatar || ''
  }

  const nameOf = (member: RoomMember) =>
    member.name || member.override?.name || charById.get(member.character_id)?.name || t('roomSeat')

  const openEditor = (member: RoomMember) => {
    setEditing(member.id)
    setMemoryDraft(member.memory ?? '')
    setAttrsDraft(JSON.stringify(member.attributes ?? {}, null, 2))
    setAttrsError('')
  }

  const saveEditor = (member: RoomMember) =>
    run(member.id, async () => {
      let attributes: Record<string, unknown>
      try {
        const parsed: unknown = attrsDraft.trim() ? JSON.parse(attrsDraft) : {}
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error(t('roomAttrsInvalid'))
        }
        attributes = parsed as Record<string, unknown>
      } catch (err) {
        setAttrsError(errorText(err))
        throw err
      }
      await updateRoomMember(sessionId, member.id, {
        memory: memoryDraft,
        attributes,
      })
      setEditing('')
    })

  const setMode = (member: RoomMember, mode: string) =>
    run(`${member.id}:mode`, async () => {
      await updateRoomMember(sessionId, member.id, {
        memoryMode: mode === '' ? null : (mode as 'rw' | 'ro'),
      })
    })

  const addSeat = () =>
    run('add', async () => {
      await addRoomMember(sessionId, addId)
      setAddId('')
    })

  const removeSeat = () => {
    if (!removing) {
      return
    }
    const target = removing
    void run(
      target.id,
      async () => {
        await removeRoomMember(sessionId, target.id)
        setRemoving(null)
      },
      t('roomMemberRemoved')
    )
  }

  if (!session) {
    return null
  }

  return (
    <>
      <Modal
        title={t('roomMembersTitle')}
        closable
        className={styles.dialog}
        mainClassName={styles.main}
        onClose={onClose}
        footer={
          <div className={styles.footer}>
            <Button variant="outlined" size="m" onClick={onClose}>
              {t('close')}
            </Button>
          </div>
        }
      >
        <div className={styles.hint}>{t('roomMembersHint')}</div>

        {userSeat ? (
          <div className={styles.seatRow}>
            <div className={styles.avatarFallback}>{(nameOf(userSeat) || '?').slice(0, 1)}</div>
            <div className={styles.seatInfo}>
              <div className={styles.nameLine}>
                <span className={styles.name}>{nameOf(userSeat)}</span>
                <span className={styles.badge}>{t('roomUserSeat')}</span>
              </div>
            </div>
          </div>
        ) : null}

        {aiSeats.map((member) => {
          const avatar = avatarOf(member)
          const open = editing === member.id
          const defer = Boolean(busy)
          return (
            <div className={styles.memberCard} key={member.id}>
              <div className={styles.seatRow}>
                {avatar ? (
                  <img className={styles.avatar} src={assetUrl(avatar)} alt="" />
                ) : (
                  <div className={styles.avatarFallback}>{nameOf(member).slice(0, 1)}</div>
                )}
                <div className={styles.seatInfo}>
                  <div className={styles.nameLine}>
                    <span className={styles.name}>{nameOf(member)}</span>
                    {member.override ? (
                      <span className={styles.badge}>{t('sessionScoped')}</span>
                    ) : null}
                  </div>
                  <div className={styles.sub}>
                    {charById.get(member.character_id)?.alias ||
                      (member.memory
                        ? member.memory.replace(/\s+/g, ' ').slice(0, 80)
                        : t('roomNoMemory'))}
                  </div>
                </div>
                <div className={styles.seatActions}>
                  <button
                    type="button"
                    className={styles.iconAction}
                    title={t('roomEditMemory')}
                    aria-label={t('roomEditMemory')}
                    onClick={() => (open ? setEditing('') : openEditor(member))}
                  >
                    <IconEditOutline16 />
                  </button>
                  <button
                    type="button"
                    className={styles.iconAction}
                    title={t('delete')}
                    aria-label={t('delete')}
                    disabled={defer}
                    onClick={() => setRemoving(member)}
                  >
                    <IconTrashOutline16 />
                  </button>
                </div>
              </div>

              <div className={styles.fieldRow}>
                <div className={styles.fieldLabel}>{t('roomCharacter')}</div>
                <div className={styles.fieldControl}>
                  <Select
                    value={member.character_id}
                    options={characterOptions}
                    disabled={defer}
                    onChange={(value) =>
                      void run(`${member.id}:character`, async () => {
                        await updateRoomMember(sessionId, member.id, { characterId: value })
                      })
                    }
                  />
                </div>
              </div>

              <div className={styles.fieldRow}>
                <div className={styles.fieldLabel}>{t('roomMemoryMode')}</div>
                <div className={styles.fieldControl}>
                  <Select
                    value={member.memory_mode ?? ''}
                    options={[
                      { value: '', label: t('roomMemoryModeRoom') },
                      { value: 'rw', label: t('roomMemoryModeRw') },
                      { value: 'ro', label: t('roomMemoryModeRo') },
                    ]}
                    disabled={defer}
                    onChange={(value) => setMode(member, value)}
                  />
                </div>
              </div>

              {open ? (
                <div className={styles.editor}>
                  <div className={styles.fieldLabel}>{t('roomMemory')}</div>
                  <textarea
                    className={styles.textarea}
                    value={memoryDraft}
                    rows={4}
                    onChange={(event) => setMemoryDraft(event.target.value)}
                  />
                  <div className={styles.fieldLabel}>{t('roomAttributes')}</div>
                  <textarea
                    className={clsx(styles.textarea, styles.mono)}
                    value={attrsDraft}
                    rows={4}
                    spellCheck={false}
                    onChange={(event) => {
                      setAttrsDraft(event.target.value)
                      setAttrsError('')
                    }}
                  />
                  {attrsError ? <div className={styles.error}>{attrsError}</div> : null}
                  <div className={styles.actions}>
                    <Button
                      variant="outlined"
                      size="s"
                      disabled={defer}
                      onClick={() =>
                        void run(
                          `${member.id}:publish`,
                          async () => {
                            await publishRoomMember(sessionId, member.id)
                          },
                          t('roomMemberPublished')
                        )
                      }
                    >
                      {t('roomPublishSeat')}
                    </Button>
                    <span className={styles.spacer} />
                    <Button variant="outlined" size="s" onClick={() => setEditing('')}>
                      {t('cancel')}
                    </Button>
                    <Button
                      size="s"
                      disabled={defer}
                      onClick={() => void saveEditor(member).catch(() => undefined)}
                    >
                      {t('loreSave')}
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          )
        })}

        {aiSeats.length === 0 ? <div className={styles.empty}>{t('roomNoAiSeats')}</div> : null}

        <div className={styles.addRow}>
          <Select
            value={addId}
            options={characterOptions}
            placeholder={t('roomAddSeatPick')}
            disabled={Boolean(busy) || characterOptions.length === 0}
            onChange={setAddId}
          />
          <Button
            variant="outlined"
            size="m"
            disabled={Boolean(busy) || !addId}
            onClick={() => void addSeat()}
          >
            <IconPlusOutline16 /> {t('roomAddSeat')}
          </Button>
        </div>

        {loadError ? <div className={styles.error}>{loadError}</div> : null}
      </Modal>

      {removing ? (
        <Modal
          title={t('roomMemberRemoveTitle')}
          closable
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button variant="outlined" size="m" onClick={() => setRemoving(null)}>
                {t('cancel')}
              </Button>
              <Button variant="danger" size="m" disabled={Boolean(busy)} onClick={removeSeat}>
                {t('delete')}
              </Button>
            </>
          }
        >
          <p>{t('roomMemberRemoveDesc', { name: nameOf(removing) })}</p>
        </Modal>
      ) : null}
    </>
  )
}