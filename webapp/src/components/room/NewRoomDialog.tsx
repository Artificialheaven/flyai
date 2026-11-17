import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { assetUrl } from '../../services/flyai/client.js'
import { useAuth } from '../../services/flyai/auth.js'
import { listCharacters, listWorlds } from '../../services/flyai/lore.js'
import type { Character, World } from '../../services/flyai/types.js'
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
  const [characters, setCharacters] = useState<Character[]>([])
  const [worlds, setWorlds] = useState<World[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set())
  const [worldId, setWorldId] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    let cancelled = false
    void Promise.all([listCharacters(), listWorlds()])
      .then(([chars, ws]) => {
        if (!cancelled) {
          setCharacters(chars)
          setWorlds(ws)
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

  const create = async () => {
    if (creating || selected.size === 0) {
      return
    }
    setCreating(true)
    setError('')
    try {
      await startRoomChat([...selected], worldId || undefined)
      closeNewRoom()
      showToast(t('newRoomCreated'))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setCreating(false)
    }
  }

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
                  <img className={styles.avatar} src={assetUrl(character.avatar)} alt="" />
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