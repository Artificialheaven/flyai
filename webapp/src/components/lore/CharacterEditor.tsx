import { useEffect, useRef, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import {
  getPublicConfig,
  listTtsVoices,
  type VoiceConfig,
} from '../../services/flyai/config.js'
import {
  generateCharacterFromText,
  suggestAttributes,
  uploadAvatar,
} from '../../services/flyai/lore.js'
import type { AttributeDef, Character, LoreEntry } from '../../services/flyai/types.js'
import { assetUrl } from '../../services/flyai/client.js'
import { Button } from '../base/Button.js'
import { Select } from '../base/Select.js'
import styles from './Lore.module.css'

type Draft = Partial<Character>

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

const emptyAttr = (): AttributeDef => ({ key: '', label: '', min: 0, max: 100, initial: 0 })

const asNumber = (value: string, fallback: number): number => {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/**
 * 角色卡编辑器：内容库新建/编辑，以及会话专属副本编辑共用。
 * initial 为 null 表示新建；readOnly 用于共享卡只读查看。
 */
export const CharacterEditor = ({
  title,
  initial,
  readOnly = false,
  backLabel,
  onBack,
  onSave,
}: {
  title: string
  initial: Character | null
  readOnly?: boolean
  backLabel: string
  onBack: () => void
  onSave: (draft: Draft) => Promise<void>
}) => {
  const t = useTranslate()
  const [draft, setDraft] = useState<Draft>(() => ({ ...(initial ?? {}) }))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [voices, setVoices] = useState<VoiceConfig[]>([])
  const [defaultSpeaker, setDefaultSpeaker] = useState('')
  const [genText, setGenText] = useState('')
  const [generating, setGenerating] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const avatarInput = useRef<HTMLInputElement>(null)

  const editing = Boolean(initial?.id)

  useEffect(() => {
    let cancelled = false
    void listTtsVoices()
      .then((data) => {
        if (!cancelled) {
          setVoices(data.voices)
          setDefaultSpeaker(data.defaultSpeaker)
        }
      })
      .catch(() => {
        // 语音清单不可用时不影响角色编辑
      })
    // 新建角色卡：把内置的对白格式 / 工具要求默认文本预填进卡片，
    // 让用户直接看到并可按角色改写（留空仍会回退到全局默认）
    if (!initial?.id && !readOnly) {
      void getPublicConfig()
        .then((cfg) => {
          const rp = (cfg.roleplay ?? {}) as {
            format_enabled?: boolean
            format_instruction?: string
            tool_instruction?: string
          }
          if (cancelled || !rp.format_enabled) {
            return
          }
          setDraft((prev) => ({
            ...prev,
            prompt_format: prev.prompt_format || rp.format_instruction || '',
            prompt_tools: prev.prompt_tools || rp.tool_instruction || '',
          }))
        })
        .catch(() => {
          // 配置不可用时保持空字段（服务端仍会注入全局默认）
        })
    }
    return () => {
      cancelled = true
    }
  }, [])

  const patch = (value: Draft) => setDraft((prev) => ({ ...prev, ...value }))
  const entries = draft.table ?? []
  const attributes = draft.attributes ?? []

  const setEntry = (index: number, value: Partial<LoreEntry>) =>
    patch({ table: entries.map((entry, i) => (i === index ? { ...entry, ...value } : entry)) })

  const setAttribute = (index: number, value: Partial<AttributeDef>) =>
    patch({
      attributes: attributes.map((attr, i) => (i === index ? { ...attr, ...value } : attr)),
    })

  const uploadAvatarFile = async (file: File) => {
    try {
      const url = await uploadAvatar(file)
      patch({ avatar: url })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const generate = async () => {
    if (!genText.trim() || generating) {
      return
    }
    setGenerating(true)
    setError('')
    try {
      const generated = await generateCharacterFromText(genText.trim())
      // 保留当前手填的头像/音色等，AI 结果作为底稿覆盖文本字段
      setDraft((prev) => ({ ...generated, ...prev, table: generated.table ?? [], attributes: generated.attributes ?? [] }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setGenerating(false)
    }
  }

  const suggest = async () => {
    if (suggesting) {
      return
    }
    setSuggesting(true)
    setError('')
    try {
      const result = await suggestAttributes({
        name: draft.name ?? '',
        intro: draft.intro ?? '',
        background: draft.background ?? '',
        existing: attributes,
      })
      patch({ attributes: result })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSuggesting(false)
    }
  }

  const save = async () => {
    if (readOnly || saving) {
      return
    }
    if (!(draft.name ?? '').trim()) {
      setError(t('loreNameRequired'))
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSave({ ...draft, table: entries, attributes })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const voiceOptions = [
    { value: '', label: `${t('loreVoiceDefault')}${defaultSpeaker ? `（${defaultSpeaker}）` : ''}` },
    ...voices.map((voice) => ({
      value: voice.id,
      label: voice.name ? `${voice.name}（${voice.id}）` : voice.id,
    })),
  ]
  // 卡片保存了清单外的音色 ID 时仍要显示当前值
  if (draft.voice && !voices.some((voice) => voice.id === draft.voice)) {
    voiceOptions.push({ value: draft.voice, label: draft.voice })
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
        {readOnly ? <div className={styles.hint}>{t('loreReadOnly')}</div> : null}
        {!editing && !readOnly ? (
          <div className={styles.genArea}>
            <div className={styles.boxTitle}>{t('loreGenFromText')}</div>
            <div className={styles.hint}>{t('loreGenFromTextDesc')}</div>
            <textarea
              className={`${styles.textarea} ${styles.textareaTall}`}
              placeholder={t('loreGenPlaceholder')}
              value={genText}
              onChange={(event) => setGenText(event.target.value)}
            />
            <div>
              <Button
                size="m"
                disabled={generating || !genText.trim()}
                onClick={() => void generate()}
              >
                {generating ? t('loreGenerating') : t('loreGenButton')}
              </Button>
            </div>
          </div>
        ) : null}

        <div className={styles.field}>
          <div className={styles.fieldLabel}>{t('loreAvatar')}</div>
          <div className={styles.avatarRow}>
            {draft.avatar ? (
              <img className={styles.avatarPreview} src={assetUrl(draft.avatar)} alt="" />
            ) : (
              <div className={styles.avatarPreview} />
            )}
            {!readOnly ? (
              <>
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
              </>
            ) : null}
          </div>
        </div>

        <div className={styles.grid2}>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('loreName')}</span>
            <input
              className={styles.input}
              value={draft.name ?? ''}
              disabled={readOnly}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('loreAlias')}</span>
            <input
              className={styles.input}
              value={draft.alias ?? ''}
              disabled={readOnly}
              onChange={(event) => patch({ alias: event.target.value })}
            />
          </label>
        </div>

        <div className={styles.field}>
          <div className={styles.fieldLabel}>{t('loreVoice')}</div>
          <Select
            value={draft.voice ?? ''}
            options={voiceOptions}
            disabled={readOnly}
            onChange={(value) => patch({ voice: value })}
          />
          <div className={styles.hint}>{t('loreVoiceHint')}</div>
        </div>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreIntro')}</span>
          <textarea
            className={`${styles.textarea} ${styles.textareaTall}`}
            value={draft.intro ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ intro: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreBackground')}</span>
          <textarea
            className={`${styles.textarea} ${styles.textareaTall}`}
            value={draft.background ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ background: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreFirstMes')}</span>
          <textarea
            className={`${styles.textarea} ${styles.textareaTall}`}
            value={draft.first_mes ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ first_mes: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreMesExample')}</span>
          <textarea
            className={styles.textarea}
            value={draft.mes_example ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ mes_example: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('loreSystemPrompt')}</span>
          <textarea
            className={styles.textarea}
            value={draft.system_prompt ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ system_prompt: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('lorePostHistory')}</span>
          <textarea
            className={styles.textarea}
            value={draft.post_history_instructions ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ post_history_instructions: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('lorePromptFormat')}</span>
          <textarea
            className={styles.textarea}
            value={draft.prompt_format ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ prompt_format: event.target.value })}
          />
          {!editing ? <div className={styles.hint}>{t('lorePromptDefaultHint')}</div> : null}
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('lorePromptTools')}</span>
          <textarea
            className={styles.textarea}
            value={draft.prompt_tools ?? ''}
            disabled={readOnly}
            onChange={(event) => patch({ prompt_tools: event.target.value })}
          />
        </label>

        <div className={styles.sectionBox}>
          <div className={styles.boxHead}>
            <div className={styles.boxTitle}>{t('loreAttributes')}</div>
            {!readOnly ? (
              <>
                <Button
                  variant="outlined"
                  size="s"
                  disabled={suggesting}
                  onClick={() => void suggest()}
                >
                  {suggesting ? t('loreGenerating') : t('loreSuggestAttr')}
                </Button>
                <Button
                  variant="outlined"
                  size="s"
                  onClick={() => patch({ attributes: [...attributes, emptyAttr()] })}
                >
                  {t('loreAddAttr')}
                </Button>
              </>
            ) : null}
          </div>
          {attributes.length === 0 ? (
            <div className={styles.hint}>{t('loreAttrEmpty')}</div>
          ) : (
            <>
              <div className={`${styles.attrRow} ${styles.attrHead}`}>
                <span>{t('loreAttrKey')}</span>
                <span>{t('loreAttrLabel')}</span>
                <span>{t('loreAttrMin')}</span>
                <span>{t('loreAttrMax')}</span>
                <span>{t('loreAttrInitial')}</span>
                <span />
              </div>
              {attributes.map((attr, index) => (
                <div className={styles.attrRow} key={index}>
                  <input
                    className={styles.attrInput}
                    value={attr.key}
                    disabled={readOnly}
                    placeholder="key"
                    onChange={(event) => setAttribute(index, { key: event.target.value })}
                  />
                  <input
                    className={styles.attrInput}
                    value={attr.label ?? ''}
                    disabled={readOnly}
                    placeholder={t('loreAttrLabel')}
                    onChange={(event) => setAttribute(index, { label: event.target.value })}
                  />
                  <input
                    className={styles.attrInput}
                    type="number"
                    value={attr.min ?? 0}
                    disabled={readOnly}
                    onChange={(event) =>
                      setAttribute(index, { min: asNumber(event.target.value, 0) })
                    }
                  />
                  <input
                    className={styles.attrInput}
                    type="number"
                    value={attr.max ?? 100}
                    disabled={readOnly}
                    onChange={(event) =>
                      setAttribute(index, { max: asNumber(event.target.value, 100) })
                    }
                  />
                  <input
                    className={styles.attrInput}
                    type="number"
                    value={attr.initial ?? 0}
                    disabled={readOnly}
                    onChange={(event) =>
                      setAttribute(index, { initial: asNumber(event.target.value, 0) })
                    }
                  />
                  {!readOnly ? (
                    <button
                      type="button"
                      className={styles.iconAction}
                      aria-label={t('delete')}
                      onClick={() =>
                        patch({ attributes: attributes.filter((_, i) => i !== index) })
                      }
                    >
                      ×
                    </button>
                  ) : (
                    <span />
                  )}
                </div>
              ))}
            </>
          )}
        </div>

        <div className={styles.sectionBox}>
          <div className={styles.boxHead}>
            <div className={styles.boxTitle}>{t('loreEntries')}</div>
            {!readOnly ? (
              <Button
                variant="outlined"
                size="s"
                onClick={() => patch({ table: [...entries, emptyEntry()] })}
              >
                {t('loreAddEntry')}
              </Button>
            ) : null}
          </div>
          {entries.length === 0 ? <div className={styles.hint}>{t('loreEntryEmpty')}</div> : null}
          {entries.map((entry, index) => (
            <div className={styles.entry} key={index}>
              <div className={styles.entryRow}>
                <input
                  className={styles.input}
                  value={entry.title}
                  disabled={readOnly}
                  placeholder={t('loreEntryTitle')}
                  onChange={(event) => setEntry(index, { title: event.target.value })}
                />
                <input
                  className={styles.input}
                  value={entry.keys.join(',')}
                  disabled={readOnly}
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
                {!readOnly ? (
                  <button
                    type="button"
                    className={styles.iconAction}
                    aria-label={t('delete')}
                    onClick={() => patch({ table: entries.filter((_, i) => i !== index) })}
                  >
                    ×
                  </button>
                ) : null}
              </div>
              <textarea
                className={styles.textarea}
                value={entry.content}
                disabled={readOnly}
                placeholder={t('loreEntryContent')}
                onChange={(event) => setEntry(index, { content: event.target.value })}
              />
              <div className={styles.entryRow}>
                <label className={styles.entryFlags}>
                  <input
                    type="checkbox"
                    checked={entry.pinned}
                    disabled={readOnly}
                    onChange={(event) => setEntry(index, { pinned: event.target.checked })}
                  />
                  {t('loreEntryPinned')}
                </label>
                <Select
                  value={entry.position}
                  disabled={readOnly}
                  options={[
                    { value: 'system', label: t('loreEntryPositionSystem') },
                    { value: 'depth', label: t('loreEntryPositionDepth') },
                  ]}
                  onChange={(value) => setEntry(index, { position: value })}
                />
                <Select
                  value={entry.role}
                  disabled={readOnly}
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
                    disabled={readOnly}
                    onChange={(event) => setEntry(index, { depth: asNumber(event.target.value, 0) })}
                  />
                </label>
                <label className={styles.entryFlags}>
                  {t('loreEntryOrder')}
                  <input
                    type="number"
                    value={entry.insert_order}
                    disabled={readOnly}
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
                    disabled={readOnly}
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
        {!readOnly ? (
          <Button size="m" disabled={saving} onClick={() => void save()}>
            {saving ? t('loreSaving') : t('loreSave')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}