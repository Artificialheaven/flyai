import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { useTranslate } from '../../i18n/index.js'
import { useAuth } from '../../services/flyai/auth.js'
import { assetUrl } from '../../services/flyai/client.js'
import {
  createCharacter,
  createWorld,
  deleteCharacter,
  deleteWorld,
  exportCharacter,
  exportWorld,
  forkCharacter,
  importCharacterCard,
  importCharacterJson,
  importWorldJson,
  importWorldbook,
  listCharacters,
  listWorlds,
  publishCharacter,
  unpublishCharacter,
  updateCharacter,
  updateWorld,
} from '../../services/flyai/lore.js'
import type { Character, World } from '../../services/flyai/types.js'
import { startRoomChat } from '../../services/chat/store.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import {
  IconCopyOutline16,
  IconDownloadOutline16,
  IconEditOutline16,
  IconNewChatOutline20,
  IconTrashOutline16,
  IconUploadOutline14,
} from '../base/icons.js'
import { CharacterEditor } from './CharacterEditor.js'
import { WorldEditor } from './WorldEditor.js'
import { closeLore, useLoreDialog, type LoreTab } from './loreState.js'
import styles from './Lore.module.css'

type View = { kind: 'list' } | { kind: 'character'; initial: Character | null } | { kind: 'world'; initial: World | null }

type ConfirmTarget =
  | { kind: 'character'; item: Character }
  | { kind: 'world'; item: World }

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

const subtitle = (text: string): string => (text || '').replace(/\s+/g, ' ').slice(0, 90)

/** 内容库：角色卡 / 世界观列表、导入导出、发布、编辑器入口。 */
export const LoreDialog = () => {
  const open = useLoreDialog((state) => state.open)
  return open ? <LoreContent /> : null
}

const LoreContent = () => {
  const t = useTranslate()
  const uid = useAuth((state) => state.user?.id ?? '')
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [tab, setTab] = useState<LoreTab>(() => useLoreDialog.getState().tab)
  const [view, setView] = useState<View>({ kind: 'list' })
  const [characters, setCharacters] = useState<Character[]>([])
  const [worlds, setWorlds] = useState<World[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')
  const [busyId, setBusyId] = useState('')
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null)
  const [deleting, setDeleting] = useState(false)
  const charFile = useRef<HTMLInputElement>(null)
  const worldFile = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [chars, ws] = await Promise.all([listCharacters(), listWorlds()])
      setCharacters(chars)
      setWorlds(ws)
    } catch (err) {
      setLoadError(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (view.kind === 'list') {
      void load()
    }
  }, [load, view.kind])

  const switchTab = (next: LoreTab) => {
    setTab(next)
    setView({ kind: 'list' })
    setQuery('')
  }

  const isShared = (character: Character) =>
    Boolean(character.public && character.author !== uid)

  const filteredChars = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return characters
    }
    return characters.filter((c) =>
      `${c.name} ${c.alias} ${c.intro}`.toLowerCase().includes(q)
    )
  }, [characters, query])

  const ownChars = filteredChars.filter((c) => !isShared(c))
  const sharedChars = filteredChars.filter(isShared)

  const filteredWorlds = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return worlds
    }
    return worlds.filter((w) => `${w.name} ${w.content}`.toLowerCase().includes(q))
  }, [worlds, query])

  const run = async (id: string, action: () => Promise<void>, doneMessage?: string) => {
    setBusyId(id)
    try {
      await action()
      if (doneMessage) {
        showToast(doneMessage)
      }
    } catch (err) {
      showToast(t('loreActionFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const startChat = (character: Character) =>
    run(character.id, async () => {
      await startRoomChat([character.id])
      closeLore()
      showToast(t('loreStartChatStarted'))
    })

  const fork = (character: Character) =>
    run(character.id, async () => {
      const copy = await forkCharacter(character.id)
      await load()
      setView({ kind: 'character', initial: copy })
      showToast(t('loreForked'))
    })

  const publish = (character: Character) =>
    run(character.id, async () => {
      if (character.public) {
        await unpublishCharacter(character.id)
      } else {
        await publishCharacter(character.id)
      }
      await load()
      showToast(character.public ? t('loreUnpublished') : t('lorePublished'))
    })

  const exportChar = (character: Character) =>
    run(character.id, async () => {
      await exportCharacter(character)
    })

  const exportWorldItem = (world: World) =>
    run(world.id, async () => {
      await exportWorld(world)
    })

  const deleteTarget = async () => {
    if (!confirmTarget || deleting) {
      return
    }
    setDeleting(true)
    try {
      if (confirmTarget.kind === 'character') {
        await deleteCharacter(confirmTarget.item.id)
      } else {
        await deleteWorld(confirmTarget.item.id)
      }
      setConfirmTarget(null)
      await load()
      showToast(t('loreDeleted'))
    } catch (err) {
      showToast(t('loreActionFailed', { message: errorText(err) }))
    } finally {
      setDeleting(false)
    }
  }

  const importCharacterFile = async (file: File) => {
    setBusyId('import')
    try {
      if (file.name.toLowerCase().endsWith('.png')) {
        await importCharacterCard(file)
      } else {
        await importCharacterJson(file)
      }
      await load()
      showToast(t('loreImported'))
    } catch (err) {
      showToast(t('loreImportFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const importWorldFile = async (file: File) => {
    setBusyId('import')
    try {
      const text = await file.text()
      let parsed: unknown = null
      try {
        parsed = JSON.parse(text)
      } catch {
        // 交给后端的容错解析
      }
      const isFlyaiWorld =
        parsed && typeof parsed === 'object' && ('type' in parsed
          ? (parsed as { type?: unknown }).type === 'flyai-world'
          : 'data' in parsed)
      if (isFlyaiWorld) {
        await importWorldJson(file)
      } else {
        await importWorldbook(file)
      }
      await load()
      showToast(t('loreImported'))
    } catch (err) {
      showToast(t('loreImportFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const saveCharacter = async (draft: Partial<Character>) => {
    if (view.kind !== 'character') {
      return
    }
    if (view.initial?.id) {
      await updateCharacter(view.initial.id, draft)
    } else {
      await createCharacter(draft)
    }
    showToast(t('loreSaved'))
    setView({ kind: 'list' })
  }

  const saveWorld = async (draft: Partial<World>) => {
    if (view.kind !== 'world') {
      return
    }
    if (view.initial?.id) {
      await updateWorld(view.initial.id, draft)
    } else {
      await createWorld(draft)
    }
    showToast(t('loreSaved'))
    setView({ kind: 'list' })
  }

  const renderCharacterCard = (character: Character, shared: boolean) => (
    <div className={styles.card} key={character.id}>
      {character.avatar ? (
        <img className={styles.avatar} src={assetUrl(character.avatar)} alt="" />
      ) : (
        <div className={styles.avatarFallback}>{(character.name || '?').slice(0, 1)}</div>
      )}
      <div className={styles.info}>
        <div className={styles.nameLine}>
          <div className={styles.name}>{character.name || character.alias || character.id}</div>
          {shared ? <span className={styles.badge}>{t('loreSharedBadge')}</span> : null}
          {character.public ? <span className={styles.badge}>{t('lorePublicBadge')}</span> : null}
        </div>
        <div className={styles.sub}>
          {subtitle(character.alias || character.intro) || t('loreNoDescription')}
        </div>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.iconAction}
          title={t('loreStartChat')}
          aria-label={t('loreStartChat')}
          disabled={busyId === character.id}
          onClick={() => void startChat(character)}
        >
          <IconNewChatOutline20 />
        </button>
        {shared ? (
          <button
            type="button"
            className={styles.iconAction}
            title={t('loreFork')}
            aria-label={t('loreFork')}
            disabled={busyId === character.id}
            onClick={() => void fork(character)}
          >
            <IconCopyOutline16 />
          </button>
        ) : (
          <button
            type="button"
            className={styles.iconAction}
            title={t('loreEdit')}
            aria-label={t('loreEdit')}
            onClick={() => setView({ kind: 'character', initial: character })}
          >
            <IconEditOutline16 />
          </button>
        )}
        <button
          type="button"
          className={styles.iconAction}
          title={t('loreExport')}
          aria-label={t('loreExport')}
          disabled={busyId === character.id}
          onClick={() => void exportChar(character)}
        >
          <IconDownloadOutline16 />
        </button>
        {isAdmin && !shared ? (
          <Button
            variant="outlined"
            size="s"
            disabled={busyId === character.id}
            onClick={() => void publish(character)}
          >
            {character.public ? t('loreUnpublish') : t('lorePublish')}
          </Button>
        ) : null}
        {!shared ? (
          <button
            type="button"
            className={styles.iconAction}
            title={t('delete')}
            aria-label={t('delete')}
            onClick={() => setConfirmTarget({ kind: 'character', item: character })}
          >
            <IconTrashOutline16 />
          </button>
        ) : null}
      </div>
    </div>
  )

  const renderWorldCard = (world: World) => (
    <div className={styles.card} key={world.id}>
      {world.avatar ? (
        <img className={styles.avatar} src={assetUrl(world.avatar)} alt="" />
      ) : (
        <div className={styles.avatarFallback}>{(world.name || '?').slice(0, 1)}</div>
      )}
      <div className={styles.info}>
        <div className={styles.nameLine}>
          <div className={styles.name}>{world.name || world.id}</div>
        </div>
        <div className={styles.sub}>
          {subtitle(world.content) || t('loreEntryCount', { count: world.table?.length ?? 0 })}
        </div>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.iconAction}
          title={t('loreEdit')}
          aria-label={t('loreEdit')}
          onClick={() => setView({ kind: 'world', initial: world })}
        >
          <IconEditOutline16 />
        </button>
        <button
          type="button"
          className={styles.iconAction}
          title={t('loreExport')}
          aria-label={t('loreExport')}
          disabled={busyId === world.id}
          onClick={() => void exportWorldItem(world)}
        >
          <IconDownloadOutline16 />
        </button>
        <button
          type="button"
          className={styles.iconAction}
          title={t('delete')}
          aria-label={t('delete')}
          onClick={() => setConfirmTarget({ kind: 'world', item: world })}
        >
          <IconTrashOutline16 />
        </button>
      </div>
    </div>
  )

  const list = (
    <>
      <div className={styles.toolbar}>
        <input
          className={styles.searchInput}
          value={query}
          placeholder={t('loreSearchPlaceholder')}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className={styles.toolbarActions}>
          {tab === 'characters' ? (
            <>
              <Button
                variant="outlined"
                size="m"
                disabled={busyId === 'import'}
                onClick={() => charFile.current?.click()}
              >
                <IconUploadOutline14 /> {t('loreImportCard')}
              </Button>
              <Button size="m" onClick={() => setView({ kind: 'character', initial: null })}>
                {t('loreNewCharacter')}
              </Button>
              <input
                ref={charFile}
                className={styles.fileInput}
                type="file"
                accept=".png,.json,image/png,application/json"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) {
                    void importCharacterFile(file)
                  }
                  event.target.value = ''
                }}
              />
            </>
          ) : (
            <>
              <Button
                variant="outlined"
                size="m"
                disabled={busyId === 'import'}
                onClick={() => worldFile.current?.click()}
              >
                <IconUploadOutline14 /> {t('loreImportWorld')}
              </Button>
              <Button size="m" onClick={() => setView({ kind: 'world', initial: null })}>
                {t('loreNewWorld')}
              </Button>
              <input
                ref={worldFile}
                className={styles.fileInput}
                type="file"
                accept=".json,application/json"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) {
                    void importWorldFile(file)
                  }
                  event.target.value = ''
                }}
              />
            </>
          )}
        </div>
      </div>

      {loadError ? <div className={styles.error}>{loadError}</div> : null}
      {loading ? (
        <div className={styles.empty}>{t('loreLoading')}</div>
      ) : tab === 'characters' ? (
        filteredChars.length === 0 ? (
          <div className={styles.empty}>{t('loreEmpty')}</div>
        ) : (
          <>
            {ownChars.length > 0 ? (
              <div className={styles.section}>
                <div className={styles.sectionTitle}>{t('loreOwnSection')}</div>
                <div className={styles.list}>
                  {ownChars.map((character) => renderCharacterCard(character, false))}
                </div>
              </div>
            ) : (
              <div className={styles.empty}>{t('loreMyCharsEmpty')}</div>
            )}
            {sharedChars.length > 0 ? (
              <div className={styles.section}>
                <div className={styles.sectionTitle}>{t('loreSharedSection')}</div>
                <div className={styles.list}>
                  {sharedChars.map((character) => renderCharacterCard(character, true))}
                </div>
              </div>
            ) : null}
          </>
        )
      ) : filteredWorlds.length === 0 ? (
        <div className={styles.empty}>{t('loreMyWorldsEmpty')}</div>
      ) : (
        <div className={styles.list}>{filteredWorlds.map(renderWorldCard)}</div>
      )}
    </>
  )

  const editor =
    view.kind === 'character' ? (
      <CharacterEditor
        title={view.initial ? view.initial.name || t('loreEditCharacter') : t('loreNewCharacter')}
        initial={view.initial}
        readOnly={Boolean(view.initial && isShared(view.initial))}
        backLabel={t('loreBack')}
        onBack={() => setView({ kind: 'list' })}
        onSave={saveCharacter}
      />
    ) : view.kind === 'world' ? (
      <WorldEditor
        title={view.initial ? view.initial.name || t('loreEditWorld') : t('loreNewWorld')}
        initial={view.initial}
        backLabel={t('loreBack')}
        onBack={() => setView({ kind: 'list' })}
        onSave={saveWorld}
      />
    ) : null

  return (
    <>
      <Modal
        title={t('lore')}
        closable
        className={styles.dialog}
        mainClassName={styles.dialogMain}
        onClose={closeLore}
      >
        <div role="tablist" className={styles.navigation}>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'characters'}
            className={clsx(styles.navItem, tab === 'characters' && styles.navItemActive)}
            onClick={() => switchTab('characters')}
          >
            {t('loreCharacters')}
            <span className={styles.badge}>{characters.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'worlds'}
            className={clsx(styles.navItem, tab === 'worlds' && styles.navItemActive)}
            onClick={() => switchTab('worlds')}
          >
            {t('loreWorlds')}
            <span className={styles.badge}>{worlds.length}</span>
          </button>
        </div>
        <div className={styles.content}>{editor ?? list}</div>
      </Modal>

      {confirmTarget ? (
        <Modal
          title={
            confirmTarget.kind === 'character'
              ? t('loreDeleteTitle')
              : t('loreDeleteWorldTitle')
          }
          closable
          onClose={() => setConfirmTarget(null)}
          footer={
            <div className={styles.editorActions}>
              <Button variant="outlined" size="m" onClick={() => setConfirmTarget(null)}>
                {t('cancel')}
              </Button>
              <Button variant="danger" size="m" disabled={deleting} onClick={() => void deleteTarget()}>
                {t('delete')}
              </Button>
            </div>
          }
        >
          <p>
            {confirmTarget.kind === 'character'
              ? t('loreDeleteDesc', { name: confirmTarget.item.name })
              : t('loreDeleteWorldDesc', { name: confirmTarget.item.name })}
          </p>
        </Modal>
      ) : null}
    </>
  )
}