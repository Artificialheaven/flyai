import type { MouseEvent } from 'react'
import type { CitationSource } from '../../types/citation.js'

/**
 * 正文里 [citation:N] 的数字角标：点击新标签打开来源。
 * P3 接入 Smart Search 时再补悬停来源卡（chat-demo 的 HoverCard/SourceCard）。
 * 角标使用带 link 角色的 span，可嵌入链接文字，样式由 .fly-markdown-cite 提供。
 */
export const CitationLink = ({ source }: { source: CitationSource }) => {
  const open = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    window.open(source.url, '_blank', 'noopener,noreferrer')
  }
  return (
    <span className="fly-markdown-cite" role="link" onClick={open}>
      {source.index}
    </span>
  )
}
