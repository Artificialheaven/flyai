import { useEffect, useMemo, useRef, useState } from 'react'
import { Avatar } from '../../../components/base/Avatar.js'
import { Button } from '../../../components/base/Button.js'
import { Modal } from '../../../components/base/Modal.js'
import { showToast } from '../../../components/base/toastState.js'
import { generateAttributes, uploadAvatar } from '../../../services/api/lore.js'
import {
  emptyCharacter,
  type AttributeDef,
  type Character,
  type CharacterPayload,
  type VoiceOption,
} from '../../../types/lore.js'
import { AttributeRows } from './AttributeRows.js'
import { EntryRows } from './EntryRows.js'
import { loadRuntimeConfig } from './configCache.js'
import styles from './Editor.module.css'

const asNumber = (value: unknown): number | undefined => {
  const parsed = Number(value)
  return value !== null && value !== '' && Number.isFinite(parsed) ? parsed : undefined
}

/** AI 建议的属性可能是任意形状，这里宽松归一，缺 key 的直接丢弃 */
const coerceAttribute = (raw: unknown): AttributeDef | null => {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }
  const obj = raw as Record<string, unknown>
  const key = typeof obj.key === 'string' ? obj.key.trim() : typeof obj.name === 'string' ? obj.name.trim() : ''
  if (!key) {
    return null
  }
  return {
    ...obj,
    key,
    label: typeof obj.label === 'string' && obj.label.trim() ? obj.label.trim() : key,
    min: asNumber(obj.min),
    max: asNumber(obj.max),
    initial: asNumber(obj.initial),
  }
}

const attrSummary = (attr: AttributeDef): string => {
  const range =
    attr.min !== undefined || attr.max !== undefined
      ? `${attr.min ?? '−∞'} ~ ${attr.max ?? '+∞'}`
      : '无数值范围'
  return `${attr.label || attr.key}（${attr.key}）· ${range}${
    attr.initial !== undefined ? ` · 初始 ${attr.initial}` : ''
  }`
}

type Draft = Character

export const CharacterEditor = ({
  initial,
  mode,
  parentId = '',
  save,
  onClose,
  onSaved,
  subtitle,
}: {
  initial?: Character
  mode: 'create' | 'edit'
  /** 新建副本时指向根卡 */
  parentId?: string
  save: (payload: CharacterPayload) => Promise<Character>
  onClose: () => void
  onSaved: (saved: Character) => void
  subtitle?: string
}) => {
  const [draft, setDraft] = useState<Draft>(() => {
    const base = initial ? { ...emptyCharacter(), ...initial } : emptyCharacter()
    if (parentId) {
      base.parent_id = parentId
      base.alias = ''
    }
    return base
  })
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const [suggestOpen, setSuggestOpen] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestions, setSuggestions] = useState<AttributeDef[]>([])
  const [picked, setPicked] = useState<boolean[]>([])

  const fileRef = useRef<HTMLInputElement>(null)

  const patch = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  useEffect(() => {
    let alive = true
    loadRuntimeConfig().then((config) => {
      if (!alive || !config) {
        return
      }
      setVoices(config.tts?.voices ?? [])
      if (mode === 'create' && !parentId) {
        // 新卡预填服务端配置的默认格式要求（旧版 characterEditor 行为）
        setDraft((current) => {
          const next = { ...current }
          let changed = false
          if (!next.prompt_format && config.roleplay?.format_instruction) {
            next.prompt_format = config.roleplay.format_instruction
            changed = true
          }
          if (!next.prompt_tools && config.roleplay?.tool_instruction) {
            next.prompt_tools = config.roleplay.tool_instruction
            changed = true
          }
          return changed ? next : current
        })
      }
    })
    return () => {
      alive = false
    }
  }, [mode, parentId])

  const voiceOptions = useMemo(() => {
    const list = [...voices]
    if (draft.voice && !list.some((voice) => voice.id === draft.voice)) {
      list.push({ name: draft.voice, id: draft.voice })
    }
    return list
  }, [voices, draft.voice])

  const pickAvatar = async (file: File | undefined) => {
    if (!file) {
      return
    }
    setUploading(true)
    setError('')
    try {
      const url = await uploadAvatar(file)
      patch('avatar', url)
    } catch (err) {
      setError(`头像上传失败：${(err as Error).message}`)
    } finally {
      setUploading(false)
      if (fileRef.current) {
        fileRef.current.value = ''
      }
    }
  }

  const openSuggest = async () => {
    setSuggestOpen(true)
    setSuggesting(true)
    setError('')
    try {
      const raw = await generateAttributes({
        name: draft.name,
        intro: draft.intro,
        background: draft.background,
        existing: draft.attributes,
      })
      const list = raw.map(coerceAttribute).filter((attr): attr is AttributeDef => attr !== null)
      setSuggestions(list)
      setPicked(list.map(() => true))
      if (list.length === 0) {
        showToast('AI 没有给出可用的属性建议')
      }
    } catch (err) {
      showToast(`属性生成失败：${(err as Error).message}`, true)
      setSuggestOpen(false)
    } finally {
      setSuggesting(false)
    }
  }

  const addPickedAttributes = () => {
    const existing = new Set(draft.attributes.map((attr) => attr.key))
    const added = suggestions.filter((attr, index) => picked[index] && !existing.has(attr.key))
    if (added.length === 0) {
      showToast('没有可添加的属性')
      return
    }
    patch('attributes', [...draft.attributes, ...added])
    setSuggestOpen(false)
    showToast(`已添加 ${added.length} 个属性`)
  }

  const submit = async () => {
    const name = draft.name.trim()
    if (!name) {
      setError('角色名不能为空')
      return
    }
    setSaving(true)
    setError('')
    const payload: CharacterPayload = {
      id: mode === 'edit' ? draft.id : undefined,
      name,
      avatar: draft.avatar.trim(),
      intro: draft.intro,
      background: draft.background,
      mes_example: draft.mes_example,
      table: draft.table,
      attributes: draft.attributes
        .map((attr) => ({ ...attr, key: attr.key.trim(), label: attr.label?.trim() || undefined }))
        .filter((attr) => attr.key),
      prompt_format: draft.prompt_format,
      prompt_tools: draft.prompt_tools,
      system_prompt: draft.system_prompt,
      post_history_instructions: draft.post_history_instructions,
      first_mes: draft.first_mes,
      parent_id: parentId || draft.parent_id,
      alias: draft.alias.trim(),
      voice: draft.voice,
    }
    try {
      const saved = await save(payload)
      onSaved(saved)
    } catch (err) {
      setError(`保存失败：${(err as Error).message}`)
      setSaving(false)
    }
  }

  const title = mode === 'create' ? (parentId ? '新建副本' : '新建角色卡') : draft.alias || draft.name || '编辑角色卡'
  const hint = subtitle ?? (parentId ? '副本与主卡独立编辑、独立发布，只继承创建时的内容。' : '角色名必填；其余字段均可留空。')

  return (
    <>
      <Modal
        title={title}
        closable
        className={styles.editor}
        mainClassName={styles.main}
        onClose={onClose}
        footer={
          <>
            <div className={styles.footerLeft}>
              {mode === 'edit' && initial?.public ? (
                <span className={styles.avatarHint}>🌐 已发布，保存后会同步更新公共版本</span>
              ) : null}
            </div>
            <Button variant="outlined" size="m" onClick={onClose}>
              取消
            </Button>
            <Button size="m" disabled={saving || uploading} onClick={() => void submit()}>
              {saving ? '保存中…' : mode === 'create' ? '创建' : '保存'}
            </Button>
          </>
        }
      >
        <p className={styles.subtitle}>{hint}</p>

        <div className={styles.avatarRow}>
          <button
            type="button"
            className={styles.avatarButton}
            title="上传头像（png/jpg/gif/webp，≤5MB）"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {draft.avatar ? (
              <Avatar src={draft.avatar} name={draft.name} size={84} />
            ) : (
              <span>{uploading ? '上传中…' : '＋ 头像'}</span>
            )}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            hidden
            onChange={(event) => void pickAvatar(event.target.files?.[0])}
          />
          <div className={styles.nameBlock}>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>角色名</span>
              <input
                className={styles.input}
                value={draft.name}
                placeholder="例如：白露"
                onChange={(event) => patch('name', event.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>别名 / 场景名</span>
              <input
                className={styles.input}
                value={draft.alias}
                placeholder="留空则显示角色名"
                onChange={(event) => patch('alias', event.target.value)}
              />
            </label>
            {draft.avatar ? (
              <button type="button" className={styles.smallButton} onClick={() => patch('avatar', '')}>
                移除头像
              </button>
            ) : (
              <span className={styles.avatarHint}>未设置头像时用名字首字占位</span>
            )}
          </div>
        </div>

        {voiceOptions.length > 0 ? (
          <label className={styles.field}>
            <span className={styles.fieldLabel}>绑定音色</span>
            <select
              className={styles.select}
              value={draft.voice}
              onChange={(event) => patch('voice', event.target.value)}
            >
              <option value="">不启用（AI 可临时指定音色）</option>
              {voiceOptions.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label className={styles.field}>
          <span className={styles.fieldLabel}>角色简介（intro）</span>
          <textarea
            className={styles.textarea}
            rows={3}
            value={draft.intro}
            placeholder="对用户展示的角色介绍"
            onChange={(event) => patch('intro', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>背景设定（background）</span>
          <textarea
            className={styles.textarea}
            rows={3}
            value={draft.background}
            placeholder="发送给 AI 的人设信息"
            onChange={(event) => patch('background', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>开场白（first_mes）</span>
          <textarea
            className={styles.textarea}
            rows={2}
            value={draft.first_mes}
            placeholder="新会话的第一条角色消息"
            onChange={(event) => patch('first_mes', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>对话示例（mes_example）</span>
          <textarea
            className={styles.textarea}
            rows={2}
            value={draft.mes_example}
            placeholder="用 &lt;START&gt; 分隔的示例对话"
            onChange={(event) => patch('mes_example', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>系统提示词（system_prompt）</span>
          <textarea
            className={styles.textarea}
            rows={2}
            value={draft.system_prompt}
            placeholder="覆盖默认的对话规则（可选）"
            onChange={(event) => patch('system_prompt', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>历史后置指令（post_history_instructions）</span>
          <textarea
            className={styles.textarea}
            rows={2}
            value={draft.post_history_instructions}
            placeholder="插在对话历史之后再喂给 AI 的补充指令"
            onChange={(event) => patch('post_history_instructions', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>格式要求（prompt_format）</span>
          <textarea
            className={styles.textarea}
            rows={3}
            value={draft.prompt_format}
            onChange={(event) => patch('prompt_format', event.target.value)}
          />
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>工具使用要求（prompt_tools）</span>
          <textarea
            className={styles.textarea}
            rows={3}
            value={draft.prompt_tools}
            onChange={(event) => patch('prompt_tools', event.target.value)}
          />
        </label>

        <div className={styles.section}>
          <h4 className={styles.sectionTitle}>动态属性</h4>
          <p className={styles.sectionHint}>
            数值属性会展示在设置面板中，AI 可通过工具修改；字段 key 为空的行不会保存。
          </p>
          <AttributeRows value={draft.attributes} onChange={(next) => patch('attributes', next)} />
          <div className={styles.rowActions}>
            <button type="button" className={styles.smallButton} onClick={() => void openSuggest()}>
              ✨ AI 生成属性建议
            </button>
            <button
              type="button"
              className={styles.smallButton}
              onClick={() =>
                patch('attributes', [...draft.attributes, { key: '', label: '', min: 0, max: 100, initial: 0 }])
              }
            >
              ＋ 添加属性
            </button>
          </div>
        </div>

        <div className={styles.section}>
          <h4 className={styles.sectionTitle}>内容表</h4>
          <p className={styles.sectionHint}>
            按触发词或常驻注入的设定条目；位置为「系统提示词」时整条注入，为「历史深度@」时按深度插入对话历史。
          </p>
          <EntryRows value={draft.table} onChange={(next) => patch('table', next)} />
        </div>

        {error ? <div className={styles.error}>{error}</div> : null}
      </Modal>

      {suggestOpen ? (
        <Modal
          title="AI 属性建议"
          closable
          className={styles.suggestDialog}
          mainClassName={styles.suggestMain}
          onClose={() => setSuggestOpen(false)}
          footer={
            <>
              <Button variant="outlined" size="m" onClick={() => setSuggestOpen(false)}>
                取消
              </Button>
              <Button size="m" disabled={suggesting} onClick={addPickedAttributes}>
                添加选中（{picked.filter(Boolean).length}）
              </Button>
            </>
          }
        >
          {suggesting ? (
            <p className={styles.sectionHint}>正在根据角色设定生成属性…</p>
          ) : (
            <div className={styles.rows}>
              {suggestions.map((attr, index) => (
                <label key={`${attr.key}-${index}`} className={styles.suggestItem}>
                  <input
                    type="checkbox"
                    checked={picked[index] ?? false}
                    onChange={(event) =>
                      setPicked((current) =>
                        current.map((value, i) => (i === index ? event.target.checked : value))
                      )
                    }
                  />
                  <span className={styles.suggestText}>{attrSummary(attr)}</span>
                </label>
              ))}
              {suggestions.length > 0 ? (
                <button
                  type="button"
                  className={styles.smallButton}
                  onClick={() => {
                    const allPicked = picked.length > 0 && picked.every(Boolean)
                    setPicked(suggestions.map(() => !allPicked))
                  }}
                >
                  全选 / 全不选
                </button>
              ) : null}
            </div>
          )}
        </Modal>
      ) : null}
    </>
  )
}