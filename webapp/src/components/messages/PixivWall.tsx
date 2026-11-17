import { useTranslate } from '../../i18n/index.js'
import clsx from 'clsx'
import { assetUrl } from '../../services/flyai/client.js'
import type { PixivCandidate } from '../../services/chat/markers.js'
import type { ToolImage } from '../../services/chat/view.js'
import styles from './PixivWall.module.css'

/**
 * P 站搜图候选墙：候选缩略图先以模糊态展示编号，AI 调用 pick_pixiv 后
 * 对应位置换成原图（reveal 为「候选编号 → 原图路径」）。
 */
export const PixivWall = ({
  candidates,
  reveal,
  onOpen,
}: {
  candidates: PixivCandidate[]
  reveal?: Record<number, string>
  onOpen: (image: ToolImage) => void
}) => {
  const t = useTranslate()
  if (!candidates.length) {
    return null
  }
  const pickedCount = candidates.filter((candidate) => reveal?.[candidate.index]).length
  return (
    <div className={styles.wall}>
      <div className={styles.header}>
        <span className={styles.title}>
          {t('pixivCandidatesTitle', { count: candidates.length })}
        </span>
        <span className={styles.state}>
          {pickedCount > 0
            ? t('pixivPickedCount', { count: pickedCount })
            : t('pixivSelecting')}
        </span>
      </div>
      <div className={styles.grid}>
        {candidates.map((candidate) => {
          const full = reveal?.[candidate.index]
          const common = (
            <>
              <img
                className={styles.image}
                loading="lazy"
                src={assetUrl(full || candidate.url)}
                alt={t('pixivCandidateAlt', { index: candidate.index })}
              />
              <span className={styles.index}>{candidate.index}</span>
              {full ? <span className={styles.picked}>{t('pixivPicked')}</span> : null}
            </>
          )
          return full ? (
            <button
              key={candidate.index}
              type="button"
              className={clsx(styles.cell, styles.revealed)}
              aria-label={t('pixivCandidateAlt', { index: candidate.index })}
              onClick={() => onOpen({ src: full, meta: null })}
            >
              {common}
            </button>
          ) : (
            <div
              key={candidate.index}
              className={clsx(styles.cell, styles.pending)}
              title={t('pixivPending')}
            >
              {common}
            </div>
          )
        })}
      </div>
    </div>
  )
}