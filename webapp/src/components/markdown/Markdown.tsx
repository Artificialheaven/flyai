import { memo, useMemo } from 'react'
import type { Source } from '../../services/chat/view.js'
import type { RegexScript } from '../../services/flyai/types.js'
import { toMdAst } from '../../services/markdown/parse.js'
import { extractCardApps } from '../../services/st/cardApp.js'
import { applyDisplayScripts } from '../../services/st/regex.js'
import { useScrollToBottom } from '../messages/scrollContext.js'
import { MarkdownAstRenderer } from './MarkdownAstRenderer.js'
import { CardAppVariablesContext } from './CardAppFrame.js'
import {
  MarkdownRenderOptionsContext,
  type MarkdownRenderOptions,
} from './SanitizedHtml.js'
import { useThrottleMarkdownRender } from './useThrottleMarkdownRender.js'
import './markdown.css'
import './highlight.css'

/** 未启用酒馆卡渲染时的缺省策略（保持旧行为） */
const DISABLED_OPTIONS: MarkdownRenderOptions = {
  htmlEnabled: false,
  externalImages: false,
  imageProxy: false,
  jsSandbox: false,
}

/**
 * 渲染链路：节流 → 酒馆展示侧正则 → 解析 AST → 渲染。
 * 传入 sources 时把正文里的 [citation:N] 渲染为角标。
 *
 * stScripts 只影响显示：复制、朗读、导出仍使用消息原文。
 */
export const Markdown = memo(function Markdown({
  content,
  loading,
  cacheKey,
  className,
  disableLink,
  sources,
  stScripts,
  stDepth,
  stHtml,
  stVariables,
}: {
  content: string
  loading: boolean
  cacheKey: string
  className?: string
  disableLink: boolean
  sources?: Source[]
  /** 当前角色卡的酒馆正则脚本（展示侧，AI 消息 placement=2） */
  stScripts?: RegexScript[]
  /** 消息距对话末尾的深度；脚本设置 minDepth/maxDepth 时生效 */
  stDepth?: number
  /** 酒馆卡 HTML/外链图片策略；缺省按全部关闭处理 */
  stHtml?: MarkdownRenderOptions
  /** MVU 变量快照：展开展示侧的 {{format_message_variable::…}} 宏 */
  stVariables?: Record<string, unknown>
}) {
  const scrollToBottom = useScrollToBottom()
  const throttledMarkdown = useThrottleMarkdownRender(content, loading)
  const displayMarkdown = useMemo(
    () => applyDisplayScripts(throttledMarkdown, stScripts, stDepth, stVariables),
    [throttledMarkdown, stScripts, stDepth, stVariables]
  )
  // 展示正则会产出带脚本的整页 HTML：抽成占位符，交给 cardApp 节点渲染
  const extracted = useMemo(() => extractCardApps(displayMarkdown), [displayMarkdown])
  const ast = useMemo(
    () =>
      extracted.text
        ? toMdAst(extracted.text, {
            sources,
            disableLink,
            cardApps: extracted.apps,
            cardMessage: content,
          })
        : null,
    [extracted, sources, disableLink, content]
  )
  return ast ? (
    <MarkdownRenderOptionsContext.Provider value={stHtml ?? DISABLED_OPTIONS}>
      <CardAppVariablesContext.Provider value={stVariables}>
        <MarkdownAstRenderer
          key={cacheKey}
          className={className}
          ast={ast}
          loading={loading}
          onUpdate={scrollToBottom}
        />
      </CardAppVariablesContext.Provider>
    </MarkdownRenderOptionsContext.Provider>
  ) : null
})
