import {
  createElement,
  memo,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import clsx from 'clsx'
import type {
  BlockContent,
  DefinitionContent,
  List as ListNode,
  Paragraph,
  PhrasingContent,
  Root,
  Table as TableNode,
} from 'mdast'
import { useTranslate } from '../../i18n/index.js'
import type { CardAppNode } from '../../services/markdown/parse.js'
import { MAX_CARD_APP_LENGTH } from '../../services/st/cardApp.js'
import { CitationLink } from '../search/CitationLink.js'
import { CardAppFrame, useCardAppVariables } from './CardAppFrame.js'
import styles from './CardAppFrame.module.css'
import { CodeBlock } from './CodeBlock.js'
import { ImageAsLink, Link } from './Link.js'
import { InlineMath, MathBlock } from './MathFormula.js'
import { MAX_HTML_LENGTH, SanitizedHtml, useMarkdownRenderOptions } from './SanitizedHtml.js'

const Html = memo(function Html({
  value,
  inline = false,
}: {
  value: string
  inline?: boolean
}) {
  if (/^<br\s*\/?>$/i.test(value)) {
    return <br />
  }
  return <SanitizedHtml value={value} inline={inline} />
})

/**
 * 卡内整页 HTML 应用：沙箱可用时进 iframe。
 *
 * 沙箱未开启（或用户关掉了 HTML 渲染）时不能把几十 KB 的文档原样摊在
 * 消息里：改为一张折叠卡片，静态内容放在限高滚动区，源码按需展开。
 */
const CardAppView = memo(function CardAppView({
  node,
  loading = false,
}: {
  node: CardAppNode
  loading?: boolean
}) {
  const { htmlEnabled, jsSandbox, externalImages } = useMarkdownRenderOptions()
  const variables = useCardAppVariables()
  const t = useTranslate()
  const [showSource, setShowSource] = useState(false)
  const tooLarge = node.html.length > MAX_CARD_APP_LENGTH
  if (htmlEnabled && jsSandbox && !tooLarge) {
    return (
      <CardAppFrame
        html={node.html}
        message={node.message}
        variables={variables}
        externalMedia={externalImages}
      />
    )
  }
  const previewable = htmlEnabled && !tooLarge && node.html.length <= MAX_HTML_LENGTH
  return (
    <div className={styles.host}>
      <div className={styles.toolbar}>
        <span className={styles.label}>{t('cardAppLabel')}</span>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.sourceBtn}
            onClick={() => setShowSource((value) => !value)}
          >
            {t(showSource ? 'cardAppHideSource' : 'cardAppShowSource')}
          </button>
        </div>
      </div>
      {previewable ? (
        <>
          <div className={styles.notice}>{t('cardAppStaticHint')}</div>
          <div className={styles.preview}>
            <SanitizedHtml value={node.html} />
          </div>
        </>
      ) : (
        <div className={styles.notice}>
          {t(tooLarge ? 'cardAppTooLarge' : 'cardAppHtmlOff')}
        </div>
      )}
      {showSource ? (
        <CodeBlock code={node.html} infostring={node.lang || 'html'} loading={loading} />
      ) : null}
    </div>
  )
})

/** 段落里只有一份卡内应用时不要包 `<p>`，避免块级应用嵌在行内元素里 */
const singleCardApp = (node: Paragraph): CardAppNode | null => {
  let found: CardAppNode | null = null
  for (const child of node.children) {
    if (child.type === 'cardApp') {
      if (found) {
        return null
      }
      found = child
    } else if (child.type !== 'text' || child.value.trim() !== '') {
      return null
    }
  }
  return found
}

const List = memo(function List({ node, loading }: { node: ListNode; loading: boolean }) {
  const ListTag = node.ordered ? 'ol' : 'ul'
  return (
    <ListTag start={node.ordered ? node.start || undefined : undefined}>
      {node.children.map((child, index) => {
        // GFM: task item 的 checked 为 boolean；普通 listItem 为 null
        const checked = child.checked
        const isTaskItem = typeof checked === 'boolean'
        return (
          <li key={index} className={isTaskItem ? 'ds-markdown-task-list-item' : undefined}>
            {isTaskItem ? (
              // □ U+25A1 / ☑ U+2611
              <span className="ds-markdown-task-checkbox">{checked ? '☑  ' : '□  '}</span>
            ) : null}
            <MarkdownBlockContentOrDefinitionContent nodes={child.children} loading={loading} />
          </li>
        )
      })}
    </ListTag>
  )
})

const Table = memo(function Table({ node }: { node: TableNode }) {
  const columnCount = node.children[0]?.children.length
  return (
    <div className="markdown-table-wrapper">
      <table>
        {node.children[0] ? (
          <thead>
            <tr>
              {node.children[0].children.map((cell, cellIndex) => {
                return (
                  <th key={cellIndex}>
                    <MarkdownPhrasingContent nodes={cell.children} />
                  </th>
                )
              })}
            </tr>
          </thead>
        ) : null}
        {node.children[1] ? (
          <tbody>
            {node.children.slice(1).map((row, rowIndex) => {
              const curColumnCount = row.children.length || 0
              return (
                <tr key={rowIndex}>
                  {row.children.map((cell, cellIndex) => {
                    return (
                      <td key={cellIndex}>
                        <MarkdownPhrasingContent nodes={cell.children} />
                      </td>
                    )
                  })}
                  {/* 补齐到列数 */}
                  {Array.from({ length: columnCount - curColumnCount }).map((_, idx) => (
                    <td key={`ptd_${idx}`} />
                  ))}
                </tr>
              )
            })}
          </tbody>
        ) : null}
      </table>
    </div>
  )
})

const MarkdownPhrasingContent = memo(function MarkdownPhrasingContent({
  nodes,
}: {
  nodes: PhrasingContent[]
}) {
  return (
    <>
      {nodes.map((node, index) => {
        return <MarkdownPhrasingContentItem key={index} node={node} />
      })}
    </>
  )
})

const MarkdownPhrasingContentItem = memo(function MarkdownPhrasingContentItem({
  node,
}: {
  node: PhrasingContent
}) {
  switch (node.type) {
    case 'inlineMath':
      return <InlineMath value={node.value} />
    case 'break':
      return <br />
    case 'emphasis':
      return (
        <em>
          <MarkdownPhrasingContent nodes={node.children} />
        </em>
      )
    case 'image':
      return <ImageAsLink node={node} />
    case 'html':
      return <Html value={node.value} inline />
    case 'inlineCode':
      return <code>{node.value}</code>
    case 'link':
      return (
        <Link node={node}>
          <MarkdownPhrasingContent nodes={node.children} />
        </Link>
      )
    case 'strong':
      return (
        <strong>
          <MarkdownPhrasingContent nodes={node.children} />
        </strong>
      )
    case 'text':
      return <TextContent>{node.value}</TextContent>
    case 'citation':
      return <CitationLink source={node.source} />
    case 'cardApp':
      return <CardAppView node={node} />
    default:
      // 删除线、脚注、引用式链接已在解析时换成文本
      return null
  }
})

// 按 \n 拆行插 <br>：源文每个换行（含软换行）都渲染为视觉换行
const TextContent = memo(function TextContent({ children }: { children: string }) {
  const result: ReactNode[] = []
  children.split(/\r?\n/).forEach((line, index) => {
    result.push(<span key={index + 's'}>{line}</span>, <br key={index + 'br'} />)
  })
  result.pop()
  return result
})

const MarkdownBlockContentOrDefinitionContent = memo(
  function MarkdownBlockContentOrDefinitionContent({
    nodes,
    loading,
  }: {
    nodes: Array<BlockContent | DefinitionContent>
    loading: boolean
  }) {
    return nodes.map((node, index) => {
      switch (node.type) {
        case 'math':
          return <MathBlock key={index} value={node.value} />
        case 'thematicBreak':
          return <hr key={index} />
        case 'blockquote':
          return (
            <blockquote key={index}>
              <MarkdownBlockContentOrDefinitionContent nodes={node.children} loading={loading} />
            </blockquote>
          )
        case 'html': {
          // 相邻 html 节点先合并再清洗，避免 <details>/<table> 等被拆段后闭合丢失
          let start = index
          while (start > 0 && nodes[start - 1].type === 'html') {
            start -= 1
          }
          if (start !== index) {
            return null
          }
          const parts: string[] = []
          let end = index
          while (end < nodes.length && nodes[end].type === 'html') {
            parts.push((nodes[end] as { value: string }).value)
            end += 1
          }
          return <Html key={index} value={parts.join('\n')} />
        }
        case 'heading':
          return node.children.length === 0
            ? null
            : createElement(
                `h${node.depth}`,
                { key: index },
                <MarkdownPhrasingContent nodes={node.children} />
              )
        case 'paragraph': {
          const app = singleCardApp(node)
          if (app) {
            return <CardAppView key={index} node={app} loading={loading} />
          }
          return (
            <p key={index} className="ds-markdown-paragraph">
              <MarkdownPhrasingContent nodes={node.children} />
            </p>
          )
        }
        case 'code':
          return (
            <CodeBlock
              key={index}
              code={node.value}
              infostring={node.lang || 'text'}
              loading={loading}
            />
          )
        case 'table':
          return <Table node={node} key={index} />
        case 'list':
          return <List key={index} node={node} loading={loading} />
        default:
          // 脚注与定义已在解析时换成段落
          return null
      }
    })
  }
)

export const MarkdownAstRenderer = memo(function MarkdownAstRenderer({
  ast,
  onUpdate,
  className,
  loading = false,
}: {
  ast: Root
  className?: string
  loading?: boolean
  /**
   * layoutEffect 之后触发 -> 进行页面滚动
   */
  onUpdate: () => void
}) {
  const onUpdateRef = useRef<() => void>(() => 0)
  onUpdateRef.current = onUpdate
  useLayoutEffect(() => {
    onUpdateRef.current()
  }, [ast.children])

  return useMemo(
    () => (
      <div className={clsx('ds-markdown', className)}>
        {/* 解析器在根节点下产出块级内容。 */}
        <MarkdownBlockContentOrDefinitionContent
          nodes={ast.children as Array<BlockContent | DefinitionContent>}
          loading={loading}
        />
      </div>
    ),
    [className, ast.children, loading]
  )
})
