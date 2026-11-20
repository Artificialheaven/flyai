import {
  IconMenuOutline20,
  IconNewChatOutline20,
  IconPanelLeftOutline16,
  IconSettingsOutline16,
  IconUsersOutline16,
} from '../base/icons.js'
import { useTranslate } from '../../i18n/index.js'
import type { Session } from '../../services/chat/types.js'
import { newChat } from '../../services/chat/store.js'
import { IconButton } from '../base/IconButton.js'
import { openMobileSider } from '../sider/mobileSiderState.js'
import { TokenMeter } from './TokenMeter.js'
import styles from './MobileHeader.module.css'

/** 移动端顶栏：左侧菜单打开侧边栏；会话页再显示标题、Token 用量、会话设置与开启新对话 */
export const MobileHeader = ({
  title,
  session,
  onOpenSettings,
  onOpenMembers,
  onOpenPanel,
}: {
  title?: string
  session?: Session
  onOpenSettings?: () => void
  onOpenMembers?: () => void
  /** 打开酒馆卡状态面板 */
  onOpenPanel?: () => void
}) => {
  const t = useTranslate()
  return (
    <div className={styles.header}>
      <IconButton
        size="xl"
        tone="primary"
        icon={<IconMenuOutline20 />}
        aria-label={t('expandSiderbarButton')}
        onClick={openMobileSider}
      />
      {title === undefined ? null : (
        <>
          <div className={styles.title}>{title}</div>
          {session ? <TokenMeter session={session} /> : null}
          {onOpenPanel ? (
            <IconButton
              size="xl"
              tone="primary"
              icon={<IconPanelLeftOutline16 />}
              aria-label={t('statusPanelOpen')}
              onClick={onOpenPanel}
            />
          ) : null}
          {onOpenMembers ? (
            <IconButton
              size="xl"
              tone="primary"
              icon={<IconUsersOutline16 />}
              aria-label={t('roomMembersTitle')}
              onClick={onOpenMembers}
            />
          ) : null}
          {onOpenSettings ? (
            <IconButton
              size="xl"
              tone="primary"
              icon={<IconSettingsOutline16 />}
              aria-label={t('sessionSettings')}
              onClick={onOpenSettings}
            />
          ) : null}
          <IconButton
            size="xl"
            tone="primary"
            icon={<IconNewChatOutline20 />}
            aria-label={t('newChat')}
            onClick={newChat}
          />
        </>
      )}
    </div>
  )
}
