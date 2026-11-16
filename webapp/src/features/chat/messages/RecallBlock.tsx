import clsx from 'clsx'
import { useState } from 'react'
import type { RecallItem } from '../../../types/session.js'
import { IconChevronDownOutline14 } from '../../../components/base/icons.js'
import styles from './MessageParts.module.css'

/** 记忆召回折叠块：一组 recall_notice / 历史 recall 渲染为一个入口 */
export const RecallBlock = ({ items, index }: { items: RecallItem[]; index: number }) => {
  const [open, setOpen] = useState(false)
  return (
    <div className={styles.recall}>
      <button type="button" className={styles.recallHead} onClick={() => setOpen((value) => !value)}>
        <span>回忆了 {items.length} 条相关记忆 #{index + 1}</span>
        <span className={clsx(styles.chevron, open && styles.chevronOpen)}>
          <IconChevronDownOutline14 />
        </span>
      </button>
      {open ? (
        <ul className={styles.recallList}>
          {items.map((item, i) => (
            <li key={i} className={styles.recallItem}>
              {item.source ? <span className={styles.recallSource}>{String(item.source)}</span> : null}
              <span className={styles.recallText}>{String(item.text ?? '')}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
