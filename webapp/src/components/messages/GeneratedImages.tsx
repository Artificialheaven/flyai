import { useTranslate } from '../../i18n/index.js'
import { assetUrl } from '../../services/flyai/client.js'
import type { ToolImage } from '../../services/chat/view.js'
import styles from './GeneratedImages.module.css'

/** 助手消息内生成的图片网格；点击交由消息气泡弹全屏 ImageViewer（带生图参数） */
export const GeneratedImages = ({
  images,
  onOpen,
}: {
  images: ToolImage[]
  onOpen: (image: ToolImage) => void
}) => {
  const t = useTranslate()
  if (!images.length) {
    return null
  }
  return (
    <div className={styles.images}>
      {images.map((image, index) => (
        <button
          key={`${image.src}-${index}`}
          type="button"
          className={styles.cell}
          onClick={() => onOpen(image)}
          aria-label={t('openImage')}
        >
          <img loading="lazy" src={assetUrl(image.src)} alt={t('generatedImageAlt')} />
        </button>
      ))}
    </div>
  )
}