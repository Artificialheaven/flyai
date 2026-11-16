import { useEffect, useRef, useState } from 'react'
import { Avatar } from '../../../components/base/Avatar.js'
import { Button } from '../../../components/base/Button.js'
import { Modal } from '../../../components/base/Modal.js'
import { uploadAvatar } from '../../../services/api/lore.js'
import {
  emptyWorld,
  type World,
  type WorldPayload,
} from '../../../types/lore.js'
import { EntryRows } from './EntryRows.js'
import { loadRuntimeConfig } from './configCache.js'
import styles from './Editor.module.css'

export const WorldEditor = ({
  initial,
  mode,
  save,
  onClose,
  onSaved,
}: {
  initial?: World
  mode: 'create' | 'edit'
  save: (payload: WorldPayload) => Promise<World>
  onClose: () => void
  onSaved: (saved: World) => void
}) => {
  const [draft, setDraft] = useState<World>(() => ({
    ...emptyWorld(),
    ...initial,
  }))
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const patch = <K extends keyof World>(key: K, value: World[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  useEffect(() => {
    if (mode !== 'create') {
      return
    }
    let alive = true
    void loadRuntimeConfig().then((config) => {
      if (!alive || !config) {
        return
      }
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
    })
    return () => {
      alive = false
    }
  }, [mode])

  const pickAvatar = async (file: File | undefined) => {
    if (!file) {
      return
    }
    setUploading(true)
    setError('')
    try {
      patch('avatar', await uploadAvatar(file))
    } catch (err) {
      setError(`头像上传失败：${(err as Error).message}`)
    } finally {
      setUploading(false)
      if (fileRef.current) {
        fileRef.current.value = ''
      }
    }
  }

  const submit = async () => {
    const name = draft.name.trim()
    if (!name) {
      setError('世界观名称不能为空')
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await save({
        id: mode === 'edit' ? draft.id : undefined,
        name,
        avatar: draft.avatar.trim(),
        content: draft.content,
        table: draft.table.map((entry) => ({ ...entry, keys: entry.keys.map((key) => key.trim()).filter(Boolean) })),
        prompt_format: draft.prompt_format,
        prompt_tools: draft.prompt_tools,
      })
      onSaved(saved)
    } catch (err) {
      setError(`保存失败：${(err as Error).message}`)
      setSaving(false)
    }
  }

  return (
    <Modal
      title={mode === 'create' ? '新建世界观' : draft.name || '编辑世界观'}
      closable
      className={styles.editor}
      mainClassName={styles.main}
      onClose={onClose}
      footer={
        <>
          <div className={styles.footerLeft} />
          <Button variant="outlined" size="m" onClick={onClose}>
            取消
          </Button>
          <Button size="m" disabled={saving || uploading} onClick={() => void submit()}>
            {saving ? '保存中…' : mode === 'create' ? '创建' : '保存'}
          </Button>
        </>
      }
    >
      <div className={styles.avatarRow}>
        <button
          type="button"
          className={styles.avatarButton}
          title="上传封面（png/jpg/gif/webp，≤5MB）"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
        >
          {draft.avatar ? (
            <Avatar src={draft.avatar} name={draft.name} size={84} />
          ) : (
            <span>{uploading ? '上传中…' : '＋ 封面'}</span>
          )}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          hidden
          onChange={(event) => void pickAvatar(event.target.files?.[0])}
        />
        <label className={styles.field} style={{ flex: 1 }}>
          <span className={styles.fieldLabel}>世界观名称</span>
          <input
            className={styles.input}
            value={draft.name}
            placeholder="例如：雾都纪事"
            onChange={(event) => patch('name', event.target.value)}
          />
        </label>
      </div>

      <label className={styles.field}>
        <span className={styles.fieldLabel}>世界观正文（content）</span>
        <textarea
          className={styles.textarea}
          rows={5}
          value={draft.content}
          placeholder="整体设定，注入到每轮对话"
          onChange={(event) => patch('content', event.target.value)}
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
        <h4 className={styles.sectionTitle}>内容表</h4>
        <p className={styles.sectionHint}>与角色卡共用条目格式，绑定该世界观后随对话注入。</p>
        <EntryRows value={draft.table} onChange={(next) => patch('table', next)} />
      </div>

      {error ? <div className={styles.error}>{error}</div> : null}
    </Modal>
  )
}