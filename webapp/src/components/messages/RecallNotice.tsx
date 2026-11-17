import { useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { IconChevronDownOutline14, IconChevronRightOutline14, IconDataOutline16 } from '../base/icons.js'
import type { RecallItem } from '../../services/chat/types.js'
import styles from './RecallNotice.module.css'

/**
 * 自动回忆：本轮自动注入的旧事实，默认收起，点击展开逐条查看。
 * 仅作展示，条目已由后端作为提示词注入，不参与本地上下文。
 */
export const RecallNotice = ({ items }: { items: RecallItem[] }) => {
  const t = useTranslate()
  const [open, setOpen] = useState(false)
  return (
    <div className={styles.recall}>
      <button type="button" className={styles.head} onClick={() => setOpen((value) => !value)}>
        <span className={styles.icon}>
          <IconDataOutline16 />
        </span>
        <span>
          {t(items.length === 1 ? 'recallNoticeOne' : 'recallNotice', { count: items.length })}
        </span>
        {open ? <IconChevronDownOutline14 /> : <IconChevronRightOutline14 />}
      </button>
      {open ? (
        <ul className={styles.list}>
          {items.map((item, index) => (
            <li key={index} className={styles.item}>
              {item.source ? <span className={styles.source}>{item.source}</span> : null}
              <span className={styles.text}>{item.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}