import clsx from 'clsx'
import { memo } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { restoreRecalledDraft } from '../../services/chat/store.js'
import type { TombstoneMessage } from '../../services/chat/types.js'
import { Button } from '../base/Button.js'
import styles from './TombstoneNotice.module.css'

/** 撤回后的整轮占位：只展示「重新编辑」入口，内容不回填到上下文。 */
export const TombstoneNotice = memo(function TombstoneNotice({
  sessionId,
  message,
  isFirst,
  busy,
}: {
  sessionId: string
  message: TombstoneMessage
  isFirst: boolean
  busy: boolean
}) {
  const t = useTranslate()
  const { content, images } = message.recalled
  return (
    <div className={clsx(styles.container, isFirst && styles.isFirst)}>
      <div className={styles.stone}>
        <div className={styles.label}>{t('tombstoneNotice')}</div>
        {content ? <div className={styles.preview}>{content}</div> : null}
        {images.length > 0 ? (
          <div className={styles.images}>{t('tombstoneImages', { count: images.length })}</div>
        ) : null}
        <Button
          size="s"
          variant="outlined"
          disabled={busy}
          onClick={() => restoreRecalledDraft(sessionId, message.recalled)}
        >
          {t('tombstoneReedit')}
        </Button>
      </div>
    </div>
  )
})