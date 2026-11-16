import type { SessionDetail } from '../../types/session.js'
import styles from './GalgameFallback.module.css'

const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: 'AI',
  system: '系统',
  tool: '工具',
}

/**
 * Galgame 模式在新版下线：历史 Galgame 会话提供只读文本兜底，
 * 不渲染输入区、不支持续聊，后端会话与路由保留。
 */
export const GalgameFallback = ({ detail }: { detail: SessionDetail }) => {
  const thread = detail.threads[0]
  const messages = thread?.messages ?? []
  const visible = messages.filter(
    (message) => !message.hidden && message.role !== 'tool' && !message.tombstone
  )

  return (
    <div className={styles.wrap}>
      <div className={styles.notice}>
        Galgame 模式已在新版下线，以下为该会话的历史消息只读记录，暂不支持继续对话。
      </div>
      <div className={styles.records}>
        {visible.length === 0 ? (
          <div className={styles.empty}>该会话没有可展示的历史消息</div>
        ) : (
          visible.map((message) => (
            <div key={message.id} className={message.role === 'user' ? styles.rowUser : styles.rowAi}>
              <span className={styles.role}>
                {message.name || ROLE_LABEL[message.role] || message.role}
              </span>
              <div className={styles.text}>{message.content || '（无文本内容）'}</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
