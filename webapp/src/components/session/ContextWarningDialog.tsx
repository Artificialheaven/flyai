import { useTranslate } from '../../i18n/index.js'
import { useContextWarning } from '../../services/chat/contextWarning.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import styles from './ContextWarningDialog.module.css'

/**
 * 上下文过长提示：只建议用户压缩/清理历史或开启新对话。
 * 后端超过硬上限后的自动截断对用户不可见，这里不提及。
 */
export const ContextWarningDialog = () => {
  const t = useTranslate()
  const warning = useContextWarning((state) => state.warning)
  const dismiss = useContextWarning((state) => state.dismiss)
  if (!warning) {
    return null
  }
  return (
    <Modal
      title={t('contextWarningTitle')}
      closable
      onClose={dismiss}
      footer={
        <Button onClick={dismiss}>{t('contextWarningDismiss')}</Button>
      }
    >
      <div className={styles.body}>
        <p>
          {t('contextWarningBody', {
            count: warning.chars.toLocaleString(),
            threshold: warning.threshold.toLocaleString(),
          })}
        </p>
      </div>
    </Modal>
  )
}
