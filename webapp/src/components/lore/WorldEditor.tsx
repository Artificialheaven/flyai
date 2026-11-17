import { useRef, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { assetUrl } from '../../services/flyai/client.js'
import { uploadAvatar } from '../../services/flyai/lore.js'
import type { LoreEntry, World } from '../../services/flyai/types.js'
import { Button } from '../base/Button.js'
import { Select } from '../base/Select.js'
import styles from './Lore.module.css'

type Draft = Partial<World>

const emptyEntry = (): LoreEntry => ({
  title: '',
  keys: [],
  content: '',
  pinned: false,
  position: 'system',
  depth: 0,
  insert_order: 0,
  role: 'system',
  probability: 100,
})

const asNumber = (value: string, fallback: number): number => {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 世界观编辑器：名称/头像/总设定 + 世界书条目。 */
export const WorldEditor = ({
  title,
  initial,
  backLabel,
  onBack,
  onSave,
}: {
  title: string
  initial: World | null
  backLabel: string
  onBack: () => void
  onSave: (draft: Draft) => Promise<void>
}) => {
  const t = useTranslate()
  const [draft, setDraft] = useState<Draft>(() => ({ ...(initial ?? {}) }))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const avatarInput = useRef<HTMLInputElement>(null)
  const entries = draft.table ?? []

  const patch = (value: Draft) => setDraft((prev) => ({ ...prev, ...value }))
  const setEntry = (index: number, value: Partial<LoreEntry>) =>
    patch({ table: entries.map((entry, i) => (i === index ? { ...entry, ...value } : entry)) })

  const uploadAvatarFile = async (file: File) => {
    try {
      patch({ avatar: await uploadAvatar(file) })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const save = async () => {
    if (saving) {
      return
    }
    if (!(draft.name ?? '').trim()) {
      setError(t('loreNameRequired'))
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSave({ ...draft, table: entries })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.editor}>
      <div className={styles.editorHead}>
        <Button variant="outlined" size="s" onClick={onBack}>
          {backLabel}
        </Button>
        <div className={styles.editorTitle}>{title}</div>
      </div>
      <div className={styles.editorScroll}>
        <div className={styles.field}>
          <div className={styles.fieldLabel}>{t('loreAvatar')}</div>
          <div className={styles.avatarRow}>
            {draft.avatar ? (
              <img className={styles.avatarPreview} src={assetUrl(draft.avatar)} alt="" />
            ) : (
              <div className={styles.avatarPreview} />
            )}
            <Button variant="outlined" size="m" onClick={() => avatarInput.current?.click()}>
              {t('loreChangeAvatar')}
            </Button>
            <input
              ref={avatarInput}
              className={styles.fileInput}
              type="file"
              accept="image/*"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) {
                  void uploadAvatarFile(file)
                }
                event.target.value = ''
              }}
            />
          </div>
        </div>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreName')}</span>
          <input
            className={styles.input}
            value={draft.name ?? ''}
            onChange={(event) => patch({ name: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreWorldContent')}</span>
          <textarea
            className={`${styles.textarea} ${styles.textareaTall}`}
            value={draft.content ?? ''}
            onChange={(event) => patch({ content: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('lorePromptFormat')}</span>
          <textarea
            className={styles.textarea}
            value={draft.prompt_format ?? ''}
            onChange={(event) => patch({ prompt_format: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('lorePromptTools')}</span>
          <textarea
            className={styles.textarea}
            value={draft.prompt_tools ?? ''}
            onChange={(event) => patch({ prompt_tools: event.target.value })}
          />
        </label>

        <div className={styles.sectionBox}>
          <div className={styles.boxHead}>
            <div className={styles.boxTitle}>{t('loreEntries')}</div>
            <Button
              variant="outlined"
              size="s"
              onClick={() => patch({ table: [...entries, emptyEntry()] })}
            >
              {t('loreAddEntry')}
            </Button>
          </div>
          {entries.length === 0 ? <div className={styles.hint}>{t('loreEntryEmpty')}</div> : null}
          {entries.map((entry, index) => (
            <div className={styles.entry} key={index}>
              <div className={styles.entryRow}>
                <input
                  className={styles.input}
                  value={entry.title}
                  placeholder={t('loreEntryTitle')}
                  onChange={(event) => setEntry(index, { title: event.target.value })}
                />
                <input
                  className={styles.input}
                  value={entry.keys.join(',')}
                  placeholder={t('loreEntryKeys')}
                  onChange={(event) =>
                    setEntry(index, {
                      keys: event.target.value
                        .split(',')
                        .map((key) => key.trim())
                        .filter(Boolean),
                    })
                  }
                />
                <button
                  type="button"
                  className={styles.iconAction}
                  aria-label={t('delete')}
                  onClick={() => patch({ table: entries.filter((_, i) => i !== index) })}
                >
                  ×
                </button>
              </div>
              <textarea
                className={styles.textarea}
                value={entry.content}
                placeholder={t('loreEntryContent')}
                onChange={(event) => setEntry(index, { content: event.target.value })}
              />
              <div className={styles.entryRow}>
                <label className={styles.entryFlags}>
                  <input
                    type="checkbox"
                    checked={entry.pinned}
                    onChange={(event) => setEntry(index, { pinned: event.target.checked })}
                  />
                  {t('loreEntryPinned')}
                </label>
                <Select
                  value={entry.position}
                  options={[
                    { value: 'system', label: t('loreEntryPositionSystem') },
                    { value: 'depth', label: t('loreEntryPositionDepth') },
                  ]}
                  onChange={(value) => setEntry(index, { position: value })}
                />
                <Select
                  value={entry.role}
                  options={[
                    { value: 'system', label: t('loreEntryRoleSystem') },
                    { value: 'user', label: t('loreEntryRoleUser') },
                    { value: 'assistant', label: t('loreEntryRoleAssistant') },
                  ]}
                  onChange={(value) => setEntry(index, { role: value })}
                />
                <label className={styles.entryFlags}>
                  {t('loreEntryDepth')}
                  <input
                    type="number"
                    value={entry.depth}
                    onChange={(event) => setEntry(index, { depth: asNumber(event.target.value, 0) })}
                  />
                </label>
                <label className={styles.entryFlags}>
                  {t('loreEntryOrder')}
                  <input
                    type="number"
                    value={entry.insert_order}
                    onChange={(event) =>
                      setEntry(index, { insert_order: asNumber(event.target.value, 0) })
                    }
                  />
                </label>
                <label className={styles.entryFlags}>
                  {t('loreEntryProb')}
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={entry.probability}
                    onChange={(event) =>
                      setEntry(index, { probability: asNumber(event.target.value, 100) })
                    }
                  />
                </label>
              </div>
            </div>
          ))}
        </div>

        {error ? <div className={styles.error}>{error}</div> : null}
      </div>
      <div className={styles.editorActions}>
        <Button variant="outlined" size="m" onClick={onBack}>
          {t('cancel')}
        </Button>
        <Button size="m" disabled={saving} onClick={() => void save()}>
          {saving ? t('loreSaving') : t('loreSave')}
        </Button>
      </div>
    </div>
  )
}