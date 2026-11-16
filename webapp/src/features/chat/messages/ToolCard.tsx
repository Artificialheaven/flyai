import clsx from 'clsx'
import { useState } from 'react'
import {
  IconCheckOutline16,
  IconChevronDownOutline14,
  IconRefreshOutline16,
  IconWarningOutline16,
} from '../../../components/base/icons.js'
import { assetUrl } from '../../../services/api/client.js'
import type { ToolStatus } from '../../../services/chat/streamReducer.js'
import type { ViewerImage } from '../../images/ImageViewer.js'
import { AudioCard } from './AudioCard.js'
import type { ToolBody } from './viewModels.js'
import styles from './MessageParts.module.css'

export type ToolView = {
  id: string
  name: string
  status: ToolStatus
  /** 状态行文案（执行中的候选数量、历史精选数量），优先于默认状态 */
  statusText?: string | null
  /** 结构化正文；无正文时只显示工具头 */
  body?: ToolBody
}

const STATUS_LABEL: Record<ToolStatus, string> = {
  pending: '等待中',
  running: '执行中',
  done: '已完成',
  error: '执行失败',
}

/** P 站候选墙：流式未精选显示模糊缩略图，历史未精选为无网络的毛玻璃占位，精选位放大展示原图 */
const PixivGrid = ({
  body,
}: {
  body: Extract<ToolBody, { kind: 'pixiv' }>
}) => (
  <div className={styles.pixivGrid}>
    {body.candidates.map((candidate) => {
      const finalUrl = body.revealed[candidate.index]
      const picked = !!finalUrl
      return (
        <figure
          key={`${candidate.index}-${candidate.pid}`}
          className={clsx(styles.pxItem, picked && styles.pxPicked)}
        >
          <div className={clsx(styles.pxFrame, picked && styles.pxRevealed)}>
            {picked ? (
              <img
                className={clsx(styles.pxImg, styles.pxRevealedImg)}
                src={assetUrl(finalUrl)}
                alt={`Pixiv 插画 ${candidate.pid}`}
                onClick={() => {
                  window.open(assetUrl(finalUrl), '_blank', 'noopener')
                }}
              />
            ) : body.live ? (
              <img
                className={clsx(styles.pxImg, styles.pxBlur)}
                src={assetUrl(candidate.url)}
                alt={`Pixiv 插画 ${candidate.pid}`}
              />
            ) : (
              <div className={clsx(styles.pxImg, styles.pxFrost)} />
            )}
            <a
              className={styles.pxLink}
              href={`https://www.pixiv.net/artworks/${candidate.pid}`}
              target="_blank"
              rel="noopener"
            >
              P站 ↗
            </a>
          </div>
        </figure>
      )
    })}
  </div>
)

/**
 * 工具调用卡：状态图标 + 名称 + 状态；
 * 文本输出可展开，生图/语音/P 站候选按标记渲染为富卡片。
 */
export const ToolCard = ({
  tool,
  onOpenImage,
}: {
  tool: ToolView
  onOpenImage: (image: ViewerImage) => void
}) => {
  const [open, setOpen] = useState(false)
  const body = tool.body
  const expandable = body?.kind === 'text' && !!body.text
  const running = tool.status === 'pending' || tool.status === 'running'
  return (
    <div className={clsx(styles.toolCard, tool.status === 'error' && styles.toolError)}>
      <button
        type="button"
        className={styles.toolHead}
        disabled={!expandable}
        onClick={() => expandable && setOpen((value) => !value)}
      >
        <span className={styles.toolIcon}>
          {running ? (
            <span className={styles.toolSpin}>
              <IconRefreshOutline16 />
            </span>
          ) : tool.status === 'error' ? (
            <IconWarningOutline16 />
          ) : (
            <IconCheckOutline16 />
          )}
        </span>
        <span className={styles.toolName}>{tool.name}</span>
        <span className={styles.toolStatus}>{tool.statusText || STATUS_LABEL[tool.status]}</span>
        {expandable ? (
          <span className={clsx(styles.chevron, open && styles.chevronOpen)}>
            <IconChevronDownOutline14 />
          </span>
        ) : null}
      </button>

      {body?.kind === 'text' && open ? <pre className={styles.toolOutput}>{body.text}</pre> : null}

      {body?.kind === 'image' ? (
        <div className={styles.toolRich}>
          <img
            className={styles.genImg}
            src={assetUrl(body.src)}
            alt="AI 生成图片"
            onClick={() => onOpenImage({ src: body.src, meta: body.meta })}
          />
        </div>
      ) : null}

      {body?.kind === 'audio' ? (
        <div className={styles.toolRich}>
          <AudioCard src={body.src} meta={body.meta} />
        </div>
      ) : null}

      {body?.kind === 'pixiv' ? (
        <div className={styles.toolRich}>
          <PixivGrid body={body} />
        </div>
      ) : null}
    </div>
  )
}