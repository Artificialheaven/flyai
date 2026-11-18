import { useTranslate } from '../../i18n/index.js'
import {
  dismissAnnouncement,
  useAnnouncement,
} from '../../services/flyai/announcement.js'
import { Button } from './Button.js'
import { Modal } from './Modal.js'
import styles from './AnnouncementDialog.module.css'

/** 启动公告：每次打开应用（含登录页）弹出；标题为空时用默认文案 */
export const AnnouncementDialog = () => {
  const t = useTranslate()
  const data = useAnnouncement((state) => state.data)
  const dismissed = useAnnouncement((state) => state.dismissed)
  if (!data || dismissed) {
    return null
  }
  return (
    <Modal
      title={data.title.trim() || t('announcementDefaultTitle')}
      className={styles.dialog}
      onClose={dismissAnnouncement}
      footer={
        <Button size="m" onClick={dismissAnnouncement}>
          {t('announcementKnow')}
        </Button>
      }
    >
      <div className={styles.content}>{data.content}</div>
    </Modal>
  )
}
