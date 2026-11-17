import { useEffect, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { useChatStore, updateSessionBinding, reloadSession } from '../../services/chat/store.js'
import { assetUrl } from '../../services/flyai/client.js'
import { listCharacters, listWorlds } from '../../services/flyai/lore.js'
import {
  clearSessionCharacter,
  exportSessionCharacter,
  forkSessionCharacter,
  publishSessionCharacter,
  saveSessionCharacter,
} from '../../services/flyai/rooms.js'
import { getSession as fetchSessionDetail } from '../../services/flyai/sessions.js'
import type { Character, World } from '../../services/flyai/types.js'
import { downloadBlob } from '../../services/native.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Select } from '../base/Select.js'
import { showToast } from '../base/toastState.js'
import { CharacterEditor } from '../lore/CharacterEditor.js'
import { SessionAssistantDialog } from './SessionAssistantDialog.js'
import styles from './SessionSettings.module.css'

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** 会话绑定（角色/世界观）、会话专属角色副本、上下文上限与工具开关。 */
export const SessionSettingsDialog = ({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) => {
  const t = useTranslate()
  const session = useChatStore((state) => state.sessionCache[sessionId])
  const [characters, setCharacters] = useState<Character[]>([])
  const [worlds, setWorlds] = useState<World[]>([])
  const [scopedChar, setScopedChar] = useState<Character | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [editingCopy, setEditingCopy] = useState(false)
  const [maxTokens, setMaxTokens] = useState(String(session?.maxTokens ?? 0))
  const [disabledTools, setDisabledTools] = useState((session?.disabledTools ?? []).join(', '))
  const [assistantOpen, setAssistantOpen] = useState(false)

  const aiSeats = session?.members?.filter((member) => !member.is_user) ?? []
  const singleAi = aiSeats.length <= 1
  const scoped = Boolean(session?.scoped)

  useEffect(() => {
    if (session) {
      setMaxTokens(String(session.maxTokens ?? 0))
      setDisabledTools((session.disabledTools ?? []).join(', '))
    }
  }, [session])

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
    return () => {
      cancelled = true
    }
  }, [])

  const loadScoped = async () => {
    try {
      const detail = await fetchSessionDetail(sessionId)
      const seat = detail.members.find((member) => !member.is_user)
      setScopedChar(detail.scoped ? seat?.override ?? detail.character : null)
    } catch {
      // 详情拉取失败不阻塞其他设置
    }
  }

  useEffect(() => {
    if (scoped) {
      void loadScoped()
    }
  }, [scoped, sessionId])

  const run = async (key: string, action: () => Promise<void>, done?: string) => {
    if (busy) {
      return
    }
    setBusy(key)
    setError('')
    try {
      await action()
      if (done) {
        showToast(done)
      }
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy('')
    }
  }

  const switchCharacter = (characterId: string) =>
    run(
      'character',
      async () => {
        await updateSessionBinding(sessionId, { characterId })
        await loadScoped()
      },
      t('sessionCharacterSwitched')
    )

  const switchWorld = (worldId: string) =>
    run(
      'world',
      async () => {
        await updateSessionBinding(sessionId, { worldId })
      },
      t('sessionWorldSwitched')
    )

  const saveSettings = () =>
    run(
      'settings',
      async () => {
        await updateSessionBinding(sessionId, {
          maxTokens: Math.max(0, Math.trunc(Number(maxTokens) || 0)),
          disabledTools: disabledTools
            .split(',')
            .map((name) => name.trim())
            .filter(Boolean),
        })
      },
      t('sessionSettingsSaved')
    )

  const forkCopy = () =>
    run('fork', async () => {
      const result = await forkSessionCharacter(sessionId)
      setScopedChar(result.character)
      await reloadSession(sessionId)
    })

  const restore = () =>
    run(
      'restore',
      async () => {
        await clearSessionCharacter(sessionId)
        setScopedChar(null)
        await reloadSession(sessionId)
      },
      t('sessionScopedRestored')
    )

  const publishCopy = () =>
    run(
      'publish',
      async () => {
        await publishSessionCharacter(sessionId)
      },
      t('sessionScopedPublished')
    )

  const exportCopy = () =>
    run('export', async () => {
      const blob = await exportSessionCharacter(sessionId)
      const name = (session?.characterName || 'character').replace(/[\\/:*?"<>|]+/g, '_')
      await downloadBlob(blob, `${name}.json`)
    })

  const saveCopy = async (draft: Partial<Character>) => {
    await saveSessionCharacter(sessionId, draft)
    await reloadSession(sessionId)
    await loadScoped()
    setEditingCopy(false)
    showToast(t('sessionScopedSaved'))
  }

  if (!session) {
    return null
  }

  if (assistantOpen) {
    return (
      <SessionAssistantDialog
        sessionId={sessionId}
        onClose={() => setAssistantOpen(false)}
      />
    )
  }

  const characterOptions = characters.map((character) => ({
    value: character.id,
    label: character.name || character.alias || character.id,
  }))
  if (session.characterId && !characters.some((c) => c.id === session.characterId)) {
    characterOptions.unshift({
      value: session.characterId,
      label: session.characterName || session.characterId,
    })
  }
  const worldOptions = [
    { value: '', label: t('sessionWorldNone') },
    ...worlds.map((world) => ({ value: world.id, label: world.name || world.id })),
  ]
  if (session.worldId && !worlds.some((w) => w.id === session.worldId)) {
    worldOptions.push({ value: session.worldId, label: session.worldName || session.worldId })
  }

  const avatar = scopedChar?.avatar || session.characterAvatar
  const name = scopedChar?.name || session.characterName
  const sub = scoped
    ? scopedChar?.alias || scopedChar?.intro || t('sessionScoped')
    : session.characterName || t('sessionNoCharacter')

  return (
    <Modal
      title={t('sessionSettings')}
      closable
      className={styles.dialog}
      mainClassName={styles.content}
      onClose={onClose}
      footer={
        <div className={styles.footer}>
          <span className={styles.spacer} />
          <Button variant="outlined" size="m" onClick={onClose}>
            {t('close')}
          </Button>
          <Button size="m" disabled={Boolean(busy)} onClick={() => void saveSettings()}>
            {busy === 'settings' ? t('loreSaving') : t('loreSave')}
          </Button>
        </div>
      }
    >
      {editingCopy && scopedChar ? (
        <CharacterEditor
          title={t('sessionScopedEdit')}
          initial={scopedChar}
          backLabel={t('loreBack')}
          onBack={() => setEditingCopy(false)}
          onSave={saveCopy}
        />
      ) : (
        <>
          <div className={styles.section}>
            <div className={styles.sectionTitle}>
              {t('sessionCharacter')}
              {scoped ? <span className={styles.badge}>{t('sessionScoped')}</span> : null}
            </div>
            <div className={styles.character}>
              {avatar ? (
                <img className={styles.avatar} src={assetUrl(avatar)} alt="" />
              ) : (
                <div className={styles.avatarFallback}>{(name || '?').slice(0, 1)}</div>
              )}
              <div className={styles.characterInfo}>
                <div className={styles.characterName}>{name || t('sessionNoCharacter')}</div>
                <div className={styles.characterSub}>{sub}</div>
              </div>
            </div>
            {singleAi && !scoped ? (
              <div className={styles.fieldRow}>
                <div className={styles.fieldLabel}>{t('sessionCharacterSwitch')}</div>
                <div className={styles.fieldControl}>
                  <Select
                    value={session.characterId ?? ''}
                    options={characterOptions}
                    disabled={Boolean(busy)}
                    placeholder={t('sessionNoCharacter')}
                    onChange={(value) => void switchCharacter(value)}
                  />
                </div>
              </div>
            ) : null}
            {!singleAi ? <div className={styles.hint}>{t('sessionGroupBindingHint')}</div> : null}
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>{t('sessionWorld')}</div>
            <div className={styles.fieldRow}>
              <div className={styles.fieldLabel}>{t('sessionWorld')}</div>
              <div className={styles.fieldControl}>
                <Select
                  value={session.worldId ?? ''}
                  options={worldOptions}
                  disabled={Boolean(busy)}
                  placeholder={t('sessionWorldNone')}
                  onChange={(value) => void switchWorld(value)}
                />
              </div>
            </div>
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>
              {t('sessionScopedSection')}
              {scoped ? <span className={styles.badge}>{t('sessionScoped')}</span> : null}
            </div>
            <div className={styles.hint}>{t('sessionScopedHint')}</div>
            <div className={styles.actions}>
              {!scoped ? (
                <Button
                  variant="outlined"
                  size="m"
                  disabled={Boolean(busy) || !singleAi}
                  onClick={() => void forkCopy()}
                >
                  {busy === 'fork' ? t('sessionScopedForking') : t('sessionScopedFork')}
                </Button>
              ) : (
                <>
                  <Button
                    variant="outlined"
                    size="m"
                    disabled={Boolean(busy) || !scopedChar}
                    onClick={() => setEditingCopy(true)}
                  >
                    {t('sessionScopedEdit')}
                  </Button>
                  <Button
                    variant="outlined"
                    size="m"
                    disabled={Boolean(busy)}
                    onClick={() => void exportCopy()}
                  >
                    {t('sessionScopedExport')}
                  </Button>
                  <Button
                    variant="outlined"
                    size="m"
                    disabled={Boolean(busy)}
                    onClick={() => void publishCopy()}
                  >
                    {t('sessionScopedPublish')}
                  </Button>
                  <Button
                    variant="dangerOutlined"
                    size="m"
                    disabled={Boolean(busy)}
                    onClick={() => void restore()}
                  >
                    {t('sessionScopedRestore')}
                  </Button>
                </>
              )}
            </div>
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>{t('sessionContext')}</div>
            <div className={styles.fieldRow}>
              <div className={styles.fieldLabel}>{t('sessionMaxTokens')}</div>
              <div className={styles.fieldControl}>
                <input
                  className={styles.input}
                  inputMode="numeric"
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value.replace(/[^\d]/g, ''))}
                />
              </div>
            </div>
            <div className={styles.hint}>{t('sessionMaxTokensHint')}</div>
            <div className={styles.fieldRow}>
              <div className={styles.fieldLabel}>{t('sessionDisabledTools')}</div>
              <div className={styles.fieldControl}>
                <input
                  className={styles.input}
                  value={disabledTools}
                  placeholder="web_search, web_fetch"
                  onChange={(event) => setDisabledTools(event.target.value)}
                />
              </div>
            </div>
            <div className={styles.hint}>{t('sessionDisabledToolsHint')}</div>
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>{t('sessionAssistant')}</div>
            <div className={styles.hint}>{t('sessionAssistantHint')}</div>
            <div className={styles.actions}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busy)}
                onClick={() => setAssistantOpen(true)}
              >
                {t('sessionAssistantOpen')}
              </Button>
            </div>
          </div>

          {error ? <div className={styles.error}>{error}</div> : null}
        </>
      )}
    </Modal>
  )
}