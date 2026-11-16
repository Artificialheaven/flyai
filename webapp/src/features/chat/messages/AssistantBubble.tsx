import clsx from 'clsx'
import { Markdown } from '../../../components/markdown/Markdown.js'
import { IconRefreshOutline16 } from '../../../components/base/icons.js'
import { useChatStore } from '../../../stores/chatStore.js'
import type { ViewerImage } from '../../images/ImageViewer.js'
import { MessageImages } from './MessageImages.js'
import { RecallBlock } from './RecallBlock.js'
import { ThinkingBlock } from './ThinkingBlock.js'
import { ToolCard } from './ToolCard.js'
import type { AssistantView } from './viewModels.js'
import styles from './Bubbles.module.css'

/**
 * AI 气泡：历史链与流式卡片共用。
 * 顺序：召回 → 思考 → 工具卡 → Markdown 正文 → 图片；群聊显示发言者名。
 */
export const AssistantBubble = ({
  view,
  showName,
  canRegenerate,
  anchorId,
  onOpenImage,
}: {
  view: AssistantView
  showName: boolean
  /** dm 历史链且当前空闲时显示重生成 */
  canRegenerate: boolean
  anchorId: string | null
  onOpenImage: (image: ViewerImage) => void
}) => {
  const regenerate = useChatStore((state) => state.regenerate)
  const busy = useChatStore((state) => state.busy)
  const hasBody = view.content.trim().length > 0

  return (
    <div className={styles.assistantRow}>
      {showName && view.name ? <div className={styles.speakerName}>{view.name}</div> : null}
      <div className={styles.assistantBubble}>
        {view.recalls.map((items, index) => (
          <RecallBlock key={index} items={items} index={index} />
        ))}
        <ThinkingBlock thinking={view.thinking} streaming={view.streaming} />
        {view.tools.map((tool) => (
          <ToolCard key={tool.id} tool={tool} onOpenImage={onOpenImage} />
        ))}
        {hasBody ? (
          <Markdown
            content={view.content}
            loading={view.streaming}
            cacheKey={`${view.key}-${view.content.length}`}
            disableLink={false}
            className="fly-markdown"
          />
        ) : view.streaming ? (
          <div className={styles.pending}>
            <span className={styles.pendingDot} />
            正在组织回复…
          </div>
        ) : null}
        {view.images.length ? <MessageImages images={view.images} onOpen={onOpenImage} /> : null}
        {canRegenerate && anchorId && !view.streaming && !busy ? (
          <div className={styles.turnActions}>
            <button
              type="button"
              className={clsx(styles.turnAction)}
              title="重新生成"
              onClick={() => regenerate(anchorId)}
            >
              <IconRefreshOutline16 />
              重新生成
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
