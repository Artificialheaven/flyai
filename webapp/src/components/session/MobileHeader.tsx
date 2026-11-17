import {
  IconMenuOutline20,
  IconNewChatOutline20,
  IconSettingsOutline16,
  IconUsersOutline16,
} from '../base/icons.js'
import { useTranslate } from '../../i18n/index.js'
import { newChat } from '../../services/chat/store.js'
import { IconButton } from '../base/IconButton.js'
import { openMobileSider } from '../sider/mobileSiderState.js'
import styles from './MobileHeader.module.css'

/** 移动端顶栏：左侧菜单打开侧边栏；会话页再显示标题、会话设置与开启新对话 */
export const MobileHeader = ({
  title,
  onOpenSettings,
  onOpenMembers,
}: {
  title?: string
  onOpenSettings?: () => void
  onOpenMembers?: () => void
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
