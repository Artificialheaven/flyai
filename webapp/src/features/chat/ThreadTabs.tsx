import clsx from 'clsx'
import type { Thread } from '../../types/session.js'
import styles from './ThreadTabs.module.css'

const kindLabel = (thread: Thread): string => thread.title || (thread.kind === 'group' ? '群聊' : '单聊')

/** 多线程房间的线程切换页签；单线程房间不渲染 */
export const ThreadTabs = ({
  threads,
  activeTid,
  onSelect,
}: {
  threads: Thread[]
  activeTid: string | null
  onSelect: (tid: string) => void
}) => {
  if (threads.length <= 1) {
    return null
  }
  return (
    <div className={styles.tabs} role="tablist">
      {threads.map((thread) => (
        <button
          key={thread.id}
          type="button"
          role="tab"
          aria-selected={thread.id === activeTid}
          className={clsx(styles.tab, thread.id === activeTid && styles.tabActive)}
          onClick={() => onSelect(thread.id)}
        >
          {kindLabel(thread)}
        </button>
      ))}
    </div>
  )
}
