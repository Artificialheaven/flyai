import { imageToDataUrl } from '../../../utils/image.js'
import type { MessageImage } from '../../../types/session.js'
import type { ViewerImage } from '../../images/ImageViewer.js'
import styles from './MessageParts.module.css'

/** 消息内图片网格；点击交由父组件弹全屏 ImageViewer（用户上传图无生图参数） */
export const MessageImages = ({
  images,
  onOpen,
}: {
  images: MessageImage[]
  onOpen: (image: ViewerImage) => void
}) => {
  if (!images.length) {
    return null
  }
  return (
    <div className={styles.imageGrid}>
      {images.map((image, index) => {
        const dataUrl = imageToDataUrl(image)
        return (
          <button
            key={index}
            type="button"
            className={styles.imageCell}
            onClick={() => onOpen({ src: dataUrl })}
            aria-label="查看大图"
          >
            <img src={dataUrl} alt="" loading="lazy" />
          </button>
        )
      })}
    </div>
  )
}
