import { useMemo } from 'react'
import { IconButton } from '../../components/base/IconButton.js'
import { IconMenuOutline20 } from '../../components/base/icons.js'
import { useChatStore, buildMentionMembers } from '../../stores/chatStore.js'
import { ChatInput } from './ChatInput.js'
import { GalgameFallback } from './GalgameFallback.js'
import { MessageList } from './MessageList.js'
import { ThreadTabs } from './ThreadTabs.js'
import { RoomMenu } from '../rooms/RoomMenu.js'
import { WelcomeState } from './WelcomeState.js'
import styles from './ChatView.module.css'

/**
 * 聊天主区：顶栏（移动端抽屉按钮 + 标题 + 线程页签）、消息流、输入区。
 * Galgame 历史会话走只读兜底，不挂载输入区与流式组件。
 */
export const ChatView = ({ onOpenSidebar }: { onOpenSidebar: () => void }) => {
  const sid = useChatStore((state) => state.sid)
  const tid = useChatStore((state) => state.tid)
  const detail = useChatStore((state) => state.detail)
  const loading = useChatStore((state) => state.loading)
  const selectThread = useChatStore((state) => state.selectThread)

  const thread = detail?.threads.find((item) => item.id === tid)
  const mentionMembers = useMemo(
    () => (detail ? buildMentionMembers(detail, thread) : []),
    [detail, thread]
  )

  if (loading) {
    return (
      <div className={styles.root}>
        <div className={styles.loading}>加载中…</div>
      </div>
    )
  }

  if (!sid || !detail) {
    return (
      <div className={styles.root}>
        <header className={styles.header}>
          <IconButton
            size="m"
            icon={<IconMenuOutline20 />}
            tooltip="打开会话列表"
            className={styles.menuButton}
            onClick={onOpenSidebar}
          />
        </header>
        <WelcomeState />
      </div>
    )
  }

  const isGalgame = detail.mode === 'galgame'

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <IconButton
          size="m"
          icon={<IconMenuOutline20 />}
          tooltip="打开会话列表"
          className={styles.menuButton}
          onClick={onOpenSidebar}
        />
        <div className={styles.titleBlock}>
          <h1 className={styles.title}>{detail.title || '未命名会话'}</h1>
          <ThreadTabs threads={detail.threads} activeTid={tid} onSelect={selectThread} />
        </div>
        {isGalgame ? null : (
          <div className={styles.headerActions}>
            <RoomMenu detail={detail} thread={thread} />
          </div>
        )}
      </header>

      {isGalgame ? (
        <GalgameFallback detail={detail} />
      ) : (
        <>
          <MessageList />
          <ChatInput mentionMembers={mentionMembers} />
        </>
      )}
    </div>
  )
}
