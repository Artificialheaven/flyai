import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { useTranslate } from '../../i18n/index.js'
import { useAuth } from '../../services/flyai/auth.js'
import { assetUrl, type UploadProgress } from '../../services/flyai/client.js'
import {
  createCharacter,
  createWorld,
  deleteCharacter,
  deleteWorld,
  exportCharacter,
  exportWorld,
  forkCharacter,
  getCharacter,
  getWorld,
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
import {
  clonePreset,
  deletePreset,
  exportPreset,
  importPreset,
  listPresets,
  setActivePreset,
  setPresetRegexDisabled,
  type PresetCatalog,
} from '../../services/flyai/presets.js'
import { peekCatalog, subscribeCatalog } from '../../services/flyai/catalogCache.js'
import type {
  Character,
  CharacterSummary,
  Preset,
  World,
  WorldSummary,
} from '../../services/flyai/types.js'
import { startRoomChat } from '../../services/chat/store.js'
import { regexScriptSide } from '../../services/st/regex.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import {
  IconCopyOutline16,
  IconDataOutline16,
  IconDownloadOutline16,
  IconEditOutline16,
  IconNewChatOutline20,
  IconSettingsOutline16,
  IconTrashOutline16,
  IconUploadOutline14,
} from '../base/icons.js'
import { CharacterEditor } from './CharacterEditor.js'
import { PresetSwitchesDialog } from './PresetSwitchesDialog.js'
import { WorldEditor } from './WorldEditor.js'
import { closeLore, useLoreDialog, type LoreTab } from './loreState.js'
import { openNewRoom } from '../room/newRoomState.js'
import styles from './Lore.module.css'

type View = { kind: 'list' } | { kind: 'character'; initial: Character | null } | { kind: 'world'; initial: World | null }

type ConfirmTarget =
  | { kind: 'character'; item: CharacterSummary }
  | { kind: 'world'; item: WorldSummary }
  | { kind: 'preset'; item: Preset }

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
  // 首帧直接用上次成功加载的目录，后台再刷新；三项都有缓存时不显示骨架
  const [initial] = useState(() => ({
    characters: peekCatalog<CharacterSummary[]>('characters'),
    worlds: peekCatalog<WorldSummary[]>('worlds'),
    presets: peekCatalog<PresetCatalog>('presets'),
  }))
  const [characters, setCharacters] = useState<CharacterSummary[]>(initial.characters ?? [])
  const [worlds, setWorlds] = useState<WorldSummary[]>(initial.worlds ?? [])
  const [presets, setPresets] = useState<Preset[]>(initial.presets?.presets ?? [])
  const [activePresetId, setActivePresetId] = useState(initial.presets?.activeId ?? '')
  const [loading, setLoading] = useState(
    initial.characters === undefined ||
      initial.worlds === undefined ||
      initial.presets === undefined
  )
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')
  const [busyId, setBusyId] = useState('')
  const [uploadState, setUploadState] = useState<{ name: string; percent: number } | null>(
    null
  )
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [regexTarget, setRegexTarget] = useState<Preset | null>(null)
  const [regexBusy, setRegexBusy] = useState(-1)
  const [switchTarget, setSwitchTarget] = useState<Preset | null>(null)
  const charFile = useRef<HTMLInputElement>(null)
  const worldFile = useRef<HTMLInputElement>(null)
  const presetFile = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    // 已有缓存时先渲染旧列表，刷新完成再替换；只有完全没有数据才显示骨架
    const seeded =
      peekCatalog('characters') !== undefined &&
      peekCatalog('worlds') !== undefined &&
      peekCatalog('presets') !== undefined
    if (!seeded) {
      setLoading(true)
    }
    setLoadError('')
    try {
      const [chars, ws, presetData] = await Promise.all([
        listCharacters(),
        listWorlds(),
        listPresets(),
      ])
      setCharacters(chars)
      setWorlds(ws)
      setPresets(presetData.presets)
      setActivePresetId(presetData.activeId)
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

  // 对话框开着时，别处（或后台刷新）更新目录也能同步到列表
  useEffect(() => {
    const offCharacters = subscribeCatalog<CharacterSummary[]>('characters', setCharacters)
    const offWorlds = subscribeCatalog<WorldSummary[]>('worlds', setWorlds)
    const offPresets = subscribeCatalog<PresetCatalog>('presets', (data) => {
      setPresets(data.presets)
      setActivePresetId(data.activeId)
    })
    return () => {
      offCharacters()
      offWorlds()
      offPresets()
    }
  }, [])

  const switchTab = (next: LoreTab) => {
    setTab(next)
    setView({ kind: 'list' })
    setQuery('')
  }

  const isShared = (character: { public?: boolean; author?: string }) =>
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

  const filteredPresets = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return presets
    }
    return presets.filter((p) => `${p.name} ${p.note}`.toLowerCase().includes(q))
  }, [presets, query])

  /** 预设一行摘要：prompt 块数量 + 绑定正则 + 温度 + 输出上限 */
  const presetMeta = (preset: Preset): string => {
    const parts: string[] = []
    if (preset.blocks.length > 0) {
      parts.push(t('lorePresetBlocks', { count: preset.blocks.length }))
    }
    const regexCount = preset.regex_scripts?.length ?? 0
    if (regexCount > 0) {
      parts.push(t('lorePresetRegexes', { count: regexCount }))
    }
    const temp = preset.params.temperature
    if (typeof temp === 'number') {
      parts.push(`temp ${temp}`)
    }
    if (preset.max_tokens > 0) {
      parts.push(t('lorePresetTokens', { count: preset.max_tokens }))
    }
    return parts.join(' · ')
  }

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

  const startChat = (character: CharacterSummary) => {
    // 有备选开场白或可选开局设定：转到新建房间弹窗（预选该卡）让用户挑；
    // 都没有则保持一键直达。摘要里的 openings 已由后端按同一规则筛好。
    if (
      (character.alternate_greetings ?? []).some((greeting) => greeting.trim()) ||
      (character.openings ?? []).length > 0
    ) {
      closeLore()
      openNewRoom([character.id])
      return
    }
    void run(character.id, async () => {
      await startRoomChat([character.id])
      closeLore()
      showToast(t('loreStartChatStarted'))
    })
  }

  /** 编辑器需要整张卡：列表只缓存摘要，点开时再拉详情 */
  const editCharacter = (character: CharacterSummary) =>
    run(character.id, async () => {
      const full = await getCharacter(character.id)
      setView({ kind: 'character', initial: full })
    })

  const editWorld = (world: WorldSummary) =>
    run(world.id, async () => {
      const full = await getWorld(world.id)
      setView({ kind: 'world', initial: full })
    })

  const fork = (character: CharacterSummary) =>
    run(character.id, async () => {
      const copy = await forkCharacter(character.id)
      await load()
      setView({ kind: 'character', initial: copy })
      showToast(t('loreForked'))
    })

  const publish = (character: CharacterSummary) =>
    run(character.id, async () => {
      if (character.public) {
        await unpublishCharacter(character.id)
      } else {
        await publishCharacter(character.id)
      }
      await load()
      showToast(character.public ? t('loreUnpublished') : t('lorePublished'))
    })

  const exportChar = (character: CharacterSummary) =>
    run(character.id, async () => {
      await exportCharacter(character)
    })

  const exportWorldItem = (world: WorldSummary) =>
    run(world.id, async () => {
      await exportWorld(world)
    })

  const togglePreset = (preset: Preset) => {
    const next = activePresetId === preset.id ? '' : preset.id
    void run(preset.id, async () => {
      const active = await setActivePreset(next)
      setActivePresetId(active)
      showToast(
        active
          ? t('lorePresetActivated', { name: preset.name })
          : t('lorePresetDeactivated')
      )
    })
  }

  const clonePresetItem = (preset: Preset) =>
    run(preset.id, async () => {
      await clonePreset(preset.id)
      await load()
      showToast(t('lorePresetCloned'))
    })

  const exportPresetItem = (preset: Preset) =>
    run(preset.id, async () => {
      await exportPreset(preset)
    })

  /** 正则/条目开关接口返回整份预设：列表与打开的弹窗同步替换 */
  const applyPresetUpdate = (updated: Preset) => {
    setPresets((list) => list.map((p) => (p.id === updated.id ? updated : p)))
    setRegexTarget((cur) => (cur && cur.id === updated.id ? updated : cur))
    setSwitchTarget((cur) => (cur && cur.id === updated.id ? updated : cur))
  }

  const toggleRegex = async (index: number, disabled: boolean) => {
    if (!regexTarget || regexBusy >= 0) {
      return
    }
    setRegexBusy(index)
    try {
      const updated = await setPresetRegexDisabled(regexTarget.id, index, disabled)
      applyPresetUpdate(updated)
    } catch (err) {
      showToast(t('loreActionFailed', { message: errorText(err) }))
    } finally {
      setRegexBusy(-1)
    }
  }

  const uploadProgress = (file: File) => (progress: UploadProgress) => {
    setUploadState({
      name: file.name,
      percent: Math.max(0, Math.min(100, Math.round(progress.percent * 100))),
    })
  }

  const importPresetFile = async (file: File) => {
    setBusyId('import')
    setUploadState({ name: file.name, percent: 0 })
    try {
      await importPreset(file, uploadProgress(file))
      await load()
      showToast(t('loreImported'))
    } catch (err) {
      showToast(t('loreImportFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
      setUploadState(null)
    }
  }

  const deleteTarget = async () => {
    if (!confirmTarget || deleting) {
      return
    }
    setDeleting(true)
    try {
      if (confirmTarget.kind === 'character') {
        await deleteCharacter(confirmTarget.item.id)
      } else if (confirmTarget.kind === 'world') {
        await deleteWorld(confirmTarget.item.id)
      } else {
        await deletePreset(confirmTarget.item.id)
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
    setUploadState({ name: file.name, percent: 0 })
    try {
      if (file.name.toLowerCase().endsWith('.png')) {
        await importCharacterCard(file, uploadProgress(file))
      } else {
        await importCharacterJson(file, uploadProgress(file))
      }
      await load()
      showToast(t('loreImported'))
    } catch (err) {
      showToast(t('loreImportFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
      setUploadState(null)
    }
  }

  const importWorldFile = async (file: File) => {
    setBusyId('import')
    setUploadState({ name: file.name, percent: 0 })
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
        await importWorldJson(file, uploadProgress(file))
      } else {
        await importWorldbook(file, uploadProgress(file))
      }
      await load()
      showToast(t('loreImported'))
    } catch (err) {
      showToast(t('loreImportFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
      setUploadState(null)
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

  const renderCharacterCard = (character: CharacterSummary, shared: boolean) => (
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
            disabled={busyId === character.id}
            onClick={() => void editCharacter(character)}
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

  const renderWorldCard = (world: WorldSummary) => (
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
          {subtitle(world.content) ||
            t('loreEntryCount', { count: world.entry_count ?? 0 })}
        </div>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.iconAction}
          title={t('loreEdit')}
          aria-label={t('loreEdit')}
          disabled={busyId === world.id}
          onClick={() => void editWorld(world)}
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

  const renderPresetCard = (preset: Preset) => {
    const active = preset.id === activePresetId
    const meta = presetMeta(preset)
    return (
      <div className={clsx(styles.card, active && styles.cardActive)} key={preset.id}>
        <div className={styles.avatarFallback}>{(preset.name || '?').slice(0, 1)}</div>
        <div className={styles.info}>
          <div className={styles.nameLine}>
            <div className={styles.name}>{preset.name || preset.id}</div>
            <span className={styles.badge}>
              {preset.source === 'tavern' ? t('lorePresetSourceTavern') : t('lorePresetSourceFlyai')}
            </span>
            {active ? (
              <span className={clsx(styles.badge, styles.badgeActive)}>{t('lorePresetActive')}</span>
            ) : null}
          </div>
          <div className={styles.sub}>{subtitle(preset.note) || meta || preset.id}</div>
          {preset.note && meta ? <div className={styles.sub}>{meta}</div> : null}
        </div>
        <div className={styles.actions}>
          <Button
            variant={active ? 'outlined' : 'primary'}
            size="s"
            disabled={busyId === preset.id}
            onClick={() => void togglePreset(preset)}
          >
            {active ? t('lorePresetDeactivate') : t('lorePresetActivate')}
          </Button>
          {preset.blocks.length > 0 ? (
            <button
              type="button"
              className={styles.iconAction}
              title={t('lorePresetSwitchesManage')}
              aria-label={t('lorePresetSwitchesManage')}
              onClick={() => setSwitchTarget(preset)}
            >
              <IconDataOutline16 />
            </button>
          ) : null}
          {(preset.regex_scripts?.length ?? 0) > 0 ? (
            <button
              type="button"
              className={styles.iconAction}
              title={t('lorePresetRegexManage')}
              aria-label={t('lorePresetRegexManage')}
              onClick={() => setRegexTarget(preset)}
            >
              <IconSettingsOutline16 />
            </button>
          ) : null}
          <button
            type="button"
            className={styles.iconAction}
            title={t('lorePresetClone')}
            aria-label={t('lorePresetClone')}
            disabled={busyId === preset.id}
            onClick={() => void clonePresetItem(preset)}
          >
            <IconCopyOutline16 />
          </button>
          <button
            type="button"
            className={styles.iconAction}
            title={t('loreExport')}
            aria-label={t('loreExport')}
            disabled={busyId === preset.id}
            onClick={() => void exportPresetItem(preset)}
          >
            <IconDownloadOutline16 />
          </button>
          <button
            type="button"
            className={styles.iconAction}
            title={t('delete')}
            aria-label={t('delete')}
            onClick={() => setConfirmTarget({ kind: 'preset', item: preset })}
          >
            <IconTrashOutline16 />
          </button>
        </div>
      </div>
    )
  }

  const list = (
    <>
      <div className={styles.toolbar}>
        <input
          className={styles.searchInput}
          value={query}
          placeholder={
            tab === 'presets' ? t('lorePresetSearchPlaceholder') : t('loreSearchPlaceholder')
          }
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
          ) : tab === 'worlds' ? (
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
          ) : (
            <>
              <Button
                variant="outlined"
                size="m"
                disabled={busyId === 'import'}
                onClick={() => presetFile.current?.click()}
              >
                <IconUploadOutline14 /> {t('lorePresetImport')}
              </Button>
              <input
                ref={presetFile}
                className={styles.fileInput}
                type="file"
                accept=".json,.txt,application/json,text/plain"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) {
                    void importPresetFile(file)
                  }
                  event.target.value = ''
                }}
              />
            </>
          )}
        </div>
      </div>

      {uploadState ? (
        <div className={styles.uploadProgress}>
          <div className={styles.uploadProgressTrack}>
            <div
              className={styles.uploadProgressBar}
              style={{ width: `${uploadState.percent}%` }}
            />
          </div>
          <span className={styles.uploadProgressText}>
            {t('loreUploading', {
              name: uploadState.name,
              percent: uploadState.percent,
            })}
          </span>
        </div>
      ) : null}
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
      ) : tab === 'worlds' ? (
        filteredWorlds.length === 0 ? (
          <div className={styles.empty}>{t('loreMyWorldsEmpty')}</div>
        ) : (
          <div className={styles.list}>{filteredWorlds.map(renderWorldCard)}</div>
        )
      ) : (
        <>
          <div className={clsx(styles.hint, styles.presetHint)}>{t('lorePresetHint')}</div>
          {filteredPresets.length === 0 ? (
            <div className={styles.empty}>
              {query.trim() ? t('loreEmpty') : t('lorePresetEmpty')}
            </div>
          ) : (
            <div className={styles.list}>{filteredPresets.map(renderPresetCard)}</div>
          )}
        </>
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

  const confirmTitle = !confirmTarget
    ? ''
    : confirmTarget.kind === 'character'
      ? t('loreDeleteTitle')
      : confirmTarget.kind === 'world'
        ? t('loreDeleteWorldTitle')
        : t('lorePresetDeleteTitle')

  const confirmDesc = !confirmTarget
    ? ''
    : confirmTarget.kind === 'character'
      ? t('loreDeleteDesc', { name: confirmTarget.item.name })
      : confirmTarget.kind === 'world'
        ? t('loreDeleteWorldDesc', { name: confirmTarget.item.name })
        : t('lorePresetDeleteDesc', { name: confirmTarget.item.name })

  const regexSideLabel = (script: { markdownOnly?: boolean; promptOnly?: boolean }): string => {
    const side = regexScriptSide(script)
    if (side === 'prompt') {
      return t('lorePresetRegexSidePrompt')
    }
    if (side === 'display') {
      return t('lorePresetRegexSideDisplay')
    }
    if (side === 'both') {
      return t('lorePresetRegexSideBoth')
    }
    return t('lorePresetRegexSideInactive')
  }

  const regexPreview = (find: unknown): string => {
    const text = typeof find === 'string' ? find.replace(/\s+/g, ' ') : ''
    return text.length > 80 ? `${text.slice(0, 80)}…` : text
  }

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
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'presets'}
            className={clsx(styles.navItem, tab === 'presets' && styles.navItemActive)}
            onClick={() => switchTab('presets')}
          >
            {t('lorePresets')}
            <span className={styles.badge}>{presets.length}</span>
          </button>
        </div>
        <div className={styles.content}>{editor ?? list}</div>
      </Modal>

      {confirmTarget ? (
        <Modal
          title={confirmTitle}
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
          <p>{confirmDesc}</p>
        </Modal>
      ) : null}

      {switchTarget ? (
        <PresetSwitchesDialog
          preset={switchTarget}
          onClose={() => setSwitchTarget(null)}
          onUpdated={applyPresetUpdate}
        />
      ) : null}

      {regexTarget ? (
        <Modal
          title={t('lorePresetRegexTitle', {
            name: regexTarget.name || regexTarget.id,
          })}
          closable
          className={styles.regexDialog}
          mainClassName={styles.regexScroll}
          onClose={() => setRegexTarget(null)}
          footer={
            <div className={styles.editorActions}>
              <Button variant="outlined" size="m" onClick={() => setRegexTarget(null)}>
                {t('close')}
              </Button>
            </div>
          }
        >
          <div className={clsx(styles.hint, styles.regexHint)}>
            {t('lorePresetRegexHint')}
          </div>
          {(regexTarget.regex_scripts?.length ?? 0) === 0 ? (
            <div className={styles.empty}>{t('lorePresetRegexEmpty')}</div>
          ) : (
            <div className={styles.regexList}>
              {(regexTarget.regex_scripts ?? []).map((script, index) => {
                const off = Boolean(script.disabled)
                const rawName = typeof script.scriptName === 'string' ? script.scriptName.trim() : ''
                const name = rawName || t('lorePresetRegexUnnamed')
                return (
                  <div
                    className={clsx(styles.regexRow, off && styles.regexRowOff)}
                    key={index}
                  >
                    <button
                      type="button"
                      role="switch"
                      aria-checked={!off}
                      aria-label={t('lorePresetRegexToggle', { name })}
                      className={clsx(styles.regexSwitch, !off && styles.regexSwitchOn)}
                      disabled={regexBusy >= 0}
                      onClick={() => void toggleRegex(index, !off)}
                    >
                      <span className={styles.regexKnob} />
                    </button>
                    <div className={styles.regexInfo}>
                      <div className={styles.regexNameLine}>
                        <span className={styles.regexName}>{name}</span>
                        <span className={styles.regexSide}>{regexSideLabel(script)}</span>
                      </div>
                      <code className={styles.regexFind}>{regexPreview(script.findRegex)}</code>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Modal>
      ) : null}
    </>
  )
}