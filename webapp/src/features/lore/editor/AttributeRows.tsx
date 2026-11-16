import type { AttributeDef } from '../../../types/lore.js'
import styles from './Editor.module.css'

const numText = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value) ? String(value) : ''

const numValue = (raw: string): number | undefined => {
  if (raw.trim() === '') {
    return undefined
  }
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : undefined
}

/** 属性行编辑器：key / 显示名 / 最小 / 最大 / 初始，空 key 行保存时被丢弃 */
export const AttributeRows = ({
  value,
  onChange,
}: {
  value: AttributeDef[]
  onChange: (next: AttributeDef[]) => void
}) => {
  const update = (index: number, patch: Partial<AttributeDef>) => {
    onChange(value.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  return (
    <div className={styles.rows}>
      {value.map((attr, index) => (
        <div className={styles.attrRow} key={index}>
          <input
            className={styles.input}
            placeholder="字段 key"
            value={attr.key ?? ''}
            onChange={(event) => update(index, { key: event.target.value })}
          />
          <input
            className={styles.input}
            placeholder="显示名"
            value={attr.label ?? ''}
            onChange={(event) => update(index, { label: event.target.value })}
          />
          <input
            className={styles.input}
            type="number"
            placeholder="最小"
            value={numText(attr.min)}
            onChange={(event) => update(index, { min: numValue(event.target.value) })}
          />
          <input
            className={styles.input}
            type="number"
            placeholder="最大"
            value={numText(attr.max)}
            onChange={(event) => update(index, { max: numValue(event.target.value) })}
          />
          <input
            className={styles.input}
            type="number"
            placeholder="初始"
            value={numText(attr.initial)}
            onChange={(event) => update(index, { initial: numValue(event.target.value) })}
          />
          <button
            type="button"
            className={styles.delButton}
            title="删除该属性"
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}