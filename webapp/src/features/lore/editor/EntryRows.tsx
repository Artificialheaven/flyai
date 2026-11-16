import { useState } from 'react'
import { emptyLoreEntry, type LoreEntry, type LoreRole } from '../../../types/lore.js'
import styles from './Editor.module.css'

/** 行内稳定标识（模型条目没有 id，删除中间行时避免受控输入串位） */
let rowSeq = 0
const nextUid = () => ++rowSeq

type Row = {
  uid: number
  entry: LoreEntry
  keysText: string
}

const toRows = (list: LoreEntry[]): Row[] =>
  list.map((entry) => ({
    uid: nextUid(),
    entry: { ...entry, keys: [...(entry.keys ?? [])] },
    keysText: (entry.keys ?? []).join(', '),
  }))

const parseKeys = (raw: string): string[] => {
  const keys: string[] = []
  for (const part of raw.split(/[\s,，、;；/]+/)) {
    const key = part.trim()
    if (key && !keys.includes(key)) {
      keys.push(key)
    }
  }
  return keys
}

const clampInt = (raw: string, min: number, max: number, fallback: number): number => {
  const parsed = parseInt(raw, 10)
  if (Number.isNaN(parsed)) {
    return fallback
  }
  return Math.max(min, Math.min(max, parsed))
}

/** 内容表条目编辑：标题/内容/常驻/触发词/插入位置与深度/角色/概率 */
export const EntryRows = ({
  value,
  onChange,
}: {
  value: LoreEntry[]
  onChange: (next: LoreEntry[]) => void
}) => {
  const [rows, setRows] = useState<Row[]>(() => toRows(value))

  const commit = (next: Row[]) => {
    setRows(next)
    onChange(next.map((row) => row.entry))
  }

  const update = (index: number, patch: Partial<LoreEntry>) => {
    commit(
      rows.map((row, i) =>
        i === index ? { ...row, entry: { ...row.entry, ...patch } } : row
      )
    )
  }

  const updateKeys = (index: number, raw: string) => {
    commit(
      rows.map((row, i) =>
        i === index
          ? { ...row, keysText: raw, entry: { ...row.entry, keys: parseKeys(raw) } }
          : row
      )
    )
  }

  const remove = (index: number) => {
    commit(rows.filter((_, i) => i !== index))
  }

  const add = () => {
    const entry = emptyLoreEntry()
    commit([...rows, { uid: nextUid(), entry, keysText: '' }])
  }

  return (
    <>
      <div className={styles.rows}>
        {rows.map((row, index) => {
          const entry = row.entry
          return (
            <div className={styles.entryRow} key={row.uid}>
              <input
                className={styles.input}
                placeholder="条目标题"
                value={entry.title}
                onChange={(event) => update(index, { title: event.target.value })}
              />
              <textarea
                className={styles.textarea}
                rows={2}
                placeholder="条目内容"
                value={entry.content}
                onChange={(event) => update(index, { content: event.target.value })}
              />
              <div className={styles.entryBar}>
                <label className={styles.inlineLabel} title="常驻：无条件注入，等同世界书 Constant">
                  <input
                    type="checkbox"
                    checked={entry.pinned}
                    onChange={(event) => update(index, { pinned: event.target.checked })}
                  />
                  常驻
                </label>
                <label className={styles.inlineLabel}>
                  位置
                  <select
                    className={styles.inlineSelect}
                    value={entry.position}
                    onChange={(event) =>
                      update(index, { position: event.target.value === 'depth' ? 'depth' : 'system' })
                    }
                  >
                    <option value="system">系统提示词</option>
                    <option value="depth">历史深度@</option>
                  </select>
                </label>
                <label className={styles.inlineLabel} title="距历史末尾的消息数（酒馆 @depth）">
                  深度
                  <input
                    className={styles.inlineInput}
                    type="number"
                    min={0}
                    max={50}
                    value={entry.depth}
                    onChange={(event) =>
                      update(index, {
                        depth: clampInt(event.target.value, 0, 50, 4),
                      })
                    }
                  />
                </label>
                <label className={styles.inlineLabel}>
                  角色
                  <select
                    className={styles.inlineSelect}
                    value={entry.role}
                    onChange={(event) =>
                      update(index, { role: event.target.value as LoreRole })
                    }
                  >
                    <option value="system">system</option>
                    <option value="user">user</option>
                    <option value="assistant">assistant</option>
                  </select>
                </label>
                <label className={styles.inlineLabel} title="每轮注入概率（酒馆 probability）">
                  概率
                  <input
                    className={styles.inlineInput}
                    type="number"
                    min={1}
                    max={100}
                    value={entry.probability}
                    onChange={(event) =>
                      update(index, {
                        probability: clampInt(event.target.value, 1, 100, 100),
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className={`${styles.delButton} ${styles.entryDel}`}
                  title="删除该条目"
                  onClick={() => remove(index)}
                >
                  ✕
                </button>
              </div>
              <input
                className={styles.input}
                placeholder="触发词（可选，逗号/顿号分隔；用户消息模糊命中时自动注入，留空则用标题匹配）"
                value={row.keysText}
                onChange={(event) => updateKeys(index, event.target.value)}
              />
            </div>
          )
        })}
      </div>
      <button type="button" className={styles.smallButton} onClick={add}>
        ＋ 添加条目
      </button>
    </>
  )
}