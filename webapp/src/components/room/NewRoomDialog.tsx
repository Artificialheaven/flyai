import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { assetUrl } from '../../services/flyai/client.js'
import { useAuth } from '../../services/flyai/auth.js'
import { listCharacters, listWorlds } from '../../services/flyai/lore.js'
import type { CharacterSummary, WorldSummary } from '../../services/flyai/types.js'
import { startRoomChat } from '../../services/chat/store.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Select } from '../base/Select.js'
import { showToast } from '../base/toastState.js'
import { IconCheckedOutline14 } from '../base/icons.js'
import { closeNewRoom, useNewRoomDialog } from './newRoomState.js'
import styles from './NewRoomDialog.module.css'

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** 新建房间：勾选多个角色卡（可选一个世界观），创建后直接进入房间会话。 */
export const NewRoomDialog = () => {
  const open = useNewRoomDialog((state) => state.open)
  return open ? <NewRoomContent /> : null
}

const NewRoomContent = () => {
  const t = useTranslate()
  const uid = useAuth((state) => state.user?.id ?? '')
  const [characters, setCharacters] = useState<CharacterSummary[]>([])
  const [worlds, setWorlds] = useState<WorldSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    () => new Set(useNewRoomDialog.getState().presetIds)
  )
  const [worldId, setWorldId] = useState('')
  const [greetingIndex, setGreetingIndex] = useState('0')
  const [openingEntry, setOpeningEntry] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    let cancelled = false
    void Promise.all([listCharacters(), listWorlds()])
      .then(([chars, ws]) => {
        if (!cancelled) {
          setCharacters(chars)
          setWorlds(ws)
          // 预选 id 可能已失效（被删除/取消共享），按当前可见列表过滤
          const known = new Set(chars.map((c) => c.id))
          setSelected((previous) => {
            const next = new Set([...previous].filter((id) => known.has(id)))
            return next.size === previous.size ? previous : next
          })
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(errorText(err))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return characters
    }
    return characters.filter((c) =>
      `${c.name} ${c.alias} ${c.intro}`.toLowerCase().includes(q)
    )
  }, [characters, query])

  const toggle = useCallback((id: string) => {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  // 只有恰好选中一张卡时才能选开场白（多角色/Galgame 后端不播种）
  const selectedCharacters = useMemo(
    () => characters.filter((c) => selected.has(c.id)),
    [characters, selected]
  )
  const singleCharacter = selectedCharacters.length === 1 ? selectedCharacters[0] : undefined
  const alternateGreetings = useMemo(() => {
    const list = singleCharacter?.alternate_greetings ?? []
    return list
      .map((text, index) => ({ index: index + 1, text: (text ?? '').trim() }))
      .filter((item) => item.text)
  }, [singleCharacter])

  // 开局设定候选（「开局*」「序章*」世界书条目）；列表接口已按同一规则筛好
  const openings = useMemo(() => singleCharacter?.openings ?? [], [singleCharacter])

  useEffect(() => {
    setGreetingIndex('0')
    setOpeningEntry('')
  }, [singleCharacter?.id, selected.size])

  const create = async () => {
    if (creating || selected.size === 0) {
      return
    }
    setCreating(true)
    setError('')
    try {
      const greeting = Number.parseInt(greetingIndex, 10)
      await startRoomChat(
        [...selected],
        worldId || undefined,
        Number.isFinite(greeting) && greeting > 0 ? greeting : undefined,
        openingEntry || undefined
      )
      closeNewRoom()
      showToast(t('newRoomCreated'))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setCreating(false)
    }
  }

  const greetingOptions = [
    { value: '0', label: t('newRoomGreetingDefault') },
    ...alternateGreetings.map((item) => ({
      value: String(item.index),
      label: t('newRoomGreetingAlt', {
        index: item.index,
        preview: item.text.replace(/\s+/g, ' ').slice(0, 40),
      }),
    })),
  ]

  const openingSelectOptions = [
    { value: '', label: t('newRoomOpeningDefault') },
    ...openings.map((item) => ({ value: item.title, label: item.title })),
  ]

  const worldOptions = [
    { value: '', label: t('sessionWorldNone') },
    ...worlds.map((world) => ({ value: world.id, label: world.name || world.id })),
  ]

  return (
    <Modal
      title={t('newRoomTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.main}
      onClose={() => {
        if (!creating) {
          closeNewRoom()
        }
      }}
      footer={
        <div className={styles.footer}>
          <span className={styles.count}>{t('newRoomSelected', { count: selected.size })}</span>
          <Button variant="outlined" size="m" onClick={closeNewRoom} disabled={creating}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={creating || selected.size === 0} onClick={() => void create()}>
            {creating ? t('newRoomCreating') : t('newRoomCreate')}
          </Button>
        </div>
      }
    >
      <div className={styles.toolbar}>
        <input
          className={styles.searchInput}
          value={query}
          placeholder={t('newRoomSearchPlaceholder')}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className={styles.worldPick}>
          <span className={styles.worldLabel}>{t('sessionWorld')}</span>
          <Select
            value={worldId}
            options={worldOptions}
            placeholder={t('sessionWorldNone')}
            disabled={creating}
            onChange={setWorldId}
          />
        </div>
        {alternateGreetings.length > 0 ? (
          <div className={styles.worldPick}>
            <span className={styles.worldLabel}>{t('newRoomGreeting')}</span>
            <Select
              value={greetingIndex}
              options={greetingOptions}
              disabled={creating}
              onChange={setGreetingIndex}
            />
          </div>
        ) : null}
        {openings.length > 0 ? (
          <div className={styles.worldPick}>
            <span className={styles.worldLabel}>{t('newRoomOpening')}</span>
            <Select
              value={openingEntry}
              options={openingSelectOptions}
              disabled={creating}
              onChange={setOpeningEntry}
            />
          </div>
        ) : null}
      </div>
      {error ? <div className={styles.error}>{error}</div> : null}
      {loading ? (
        <div className={styles.empty}>{t('loreLoading')}</div>
      ) : filtered.length === 0 ? (
        <div className={styles.empty}>{t('newRoomEmpty')}</div>
      ) : (
        <div className={styles.list} role="listbox" aria-multiselectable="true">
          {filtered.map((character) => {
            const checked = selected.has(character.id)
            const shared = Boolean(character.public && character.author !== uid)
            return (
              <button
                key={character.id}
                type="button"
                role="option"
                aria-selected={checked}
                className={clsx(styles.card, checked && styles.cardSelected)}
                onClick={() => toggle(character.id)}
                disabled={creating}
              >
                {character.avatar ? (
                  <img className={styles.avatar} src={assetUrl(character.avatar_thumb || character.avatar)} alt="" />
                ) : (
                  <div className={styles.avatarFallback}>
                    {(character.name || '?').slice(0, 1)}
                  </div>
                )}
                <div className={styles.info}>
                  <div className={styles.nameLine}>
                    <span className={styles.name}>
                      {character.name || character.alias || character.id}
                    </span>
                    {shared ? <span className={styles.badge}>{t('loreSharedBadge')}</span> : null}
                  </div>
                  <div className={styles.sub}>
                    {(character.alias || character.intro || '')
                      .replace(/\s+/g, ' ')
                      .slice(0, 90) || t('loreNoDescription')}
                  </div>
                </div>
                <span className={clsx(styles.check, checked && styles.checkOn)}>
                  {checked ? <IconCheckedOutline14 /> : null}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </Modal>
  )
}