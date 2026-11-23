import clsx from 'clsx'
import { useState } from 'react'
import { useIsMobile } from '../../app/viewport.js'
import { useTranslate } from '../../i18n/index.js'
import type { Session } from '../../services/chat/types.js'
import {
  IconPanelLeftOutline16,
  IconSettingsOutline16,
  IconUsersOutline16,
} from '../base/icons.js'
import { IconButton } from '../base/IconButton.js'
import { ChatInput } from '../chatInput/ChatInput.js'
import { MessageList } from '../messages/MessageList.js'
import { RoomMembersDialog } from '../room/RoomMembersDialog.js'
import { ContextWarningDialog } from './ContextWarningDialog.js'
import { MobileHeader } from './MobileHeader.js'
import { SessionSettingsDialog } from './SessionSettingsDialog.js'
import { StatusPanelDrawer, statusPanelAvailable } from './StatusPanel.js'
import { ThreadTabs } from './ThreadTabs.js'
import { CardSyncBar } from '../base/CardSyncBar.js'
import { TokenMeter } from './TokenMeter.js'
import styles from './SessionView.module.css'

/** 会话页：标题栏（移动端为顶栏）+ 消息列表，输入框吸底 */
export const SessionView = ({
  session,
  streaming,
  siderCollapsed,
}: {
  session: Session
  streaming: boolean
  siderCollapsed: boolean
}) => {
  const t = useTranslate()
  const isMobile = useIsMobile()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [membersOpen, setMembersOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const title = session.title || t('untitledChat')
  const canManageMembers = (session.members?.length ?? 0) > 0
  const hasPanel = statusPanelAvailable(session)
  return (
    <div className={styles.view}>
      {isMobile ? (
        <MobileHeader
          title={title}
          session={session}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenMembers={canManageMembers ? () => setMembersOpen(true) : undefined}
          onOpenPanel={hasPanel ? () => setPanelOpen(true) : undefined}
        />
      ) : (
        <div className={styles.header}>
          <div className={clsx(styles.title, siderCollapsed && styles.withCollapsedSider)}>
            {title}
          </div>
          <div className={styles.headerRight}>
            {hasPanel ? (
              <IconButton
                size="m"
                icon={<IconPanelLeftOutline16 />}
                tooltip={t('statusPanelOpen')}
                aria-label={t('statusPanelOpen')}
                onClick={() => setPanelOpen(true)}
              />
            ) : null}
            <TokenMeter session={session} />
            {canManageMembers ? (
              <IconButton
                size="m"
                icon={<IconUsersOutline16 />}
                tooltip={t('roomMembersTitle')}
                aria-label={t('roomMembersTitle')}
                onClick={() => setMembersOpen(true)}
              />
            ) : null}
            <IconButton
              size="m"
              icon={<IconSettingsOutline16 />}
              tooltip={t('sessionSettings')}
              aria-label={t('sessionSettings')}
              onClick={() => setSettingsOpen(true)}
            />
          </div>
        </div>
      )}
      <ThreadTabs sessionId={session.id} />
      {/* 卡片写回进度条挂在头部（含标签栏）下沿，而不是会话内容里 */}
      <CardSyncBar />
      {membersOpen ? (
        <RoomMembersDialog sessionId={session.id} onClose={() => setMembersOpen(false)} />
      ) : null}
      <ContextWarningDialog />
      {settingsOpen ? (
        <SessionSettingsDialog
          sessionId={session.id}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}
      {panelOpen ? (
        <StatusPanelDrawer session={session} onClose={() => setPanelOpen(false)} />
      ) : null}
      <MessageList
        session={session}
        streaming={streaming}
        footer={
          session.readOnly ? (
            // 历史 galgame 会话暂不支持续聊，只作只读回看
            <div className={styles.inputContainer}>
              <div className={styles.readOnlyHint}>{t('galgameReadOnlyHint')}</div>
            </div>
          ) : (
            <div className={styles.inputContainer}>
              <ChatInput sessionId={session.id} />
              <div className={styles.caveat}>{t('contentCaveat')}</div>
            </div>
          )
        }
      />
    </div>
  )
}
