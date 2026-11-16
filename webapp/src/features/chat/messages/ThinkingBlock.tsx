import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'
import { IconChevronDownOutline14 } from '../../../components/base/icons.js'
import styles from './MessageParts.module.css'

/** 思考过程折叠块：流式中默认展开，历史消息默认收起 */
export const ThinkingBlock = ({ thinking, streaming }: { thinking: string; streaming: boolean }) => {
  const [open, setOpen] = useState(streaming)
  const wasStreaming = useRef(streaming)
  useEffect(() => {
    // 流结束的瞬间自动收起，历史回看保持整洁
    if (wasStreaming.current && !streaming) {
      setOpen(false)
    }
    wasStreaming.current = streaming
  }, [streaming])

  if (!thinking) {
    return null
  }
  return (
    <div className={styles.thinking}>
      <button type="button" className={styles.thinkingHead} onClick={() => setOpen((value) => !value)}>
        <span>思考过程</span>
        <span className={clsx(styles.chevron, open && styles.chevronOpen)}>
          <IconChevronDownOutline14 />
        </span>
      </button>
      {open ? <div className={styles.thinkingBody}>{thinking}</div> : null}
    </div>
  )
}
