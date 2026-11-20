/**
 * 酒馆卡消息里的受控 HTML（状态面板等）。
 *
 * 只做「白名单渲染」：DOMPurify 移除脚本/事件/危险标签，再按
 * `htmlPolicy` 收紧链接、图片、style 与 class。开关关闭时保持旧的转义文本。
 * 卡片声明的一切 JavaScript 都不会执行。
 */

import DOMPurify from 'dompurify'
import {
  createContext,
  memo,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { assetUrl } from '../../services/flyai/client.js'
import { fetchProxyImage } from '../../services/st/imageProxy.js'
import { ImageViewer, type ViewerImage } from '../images/ImageViewer.js'
import {
  filterSafeClasses,
  filterSafeStyle,
  isExternalImage,
  safeImageSrc,
  safeLinkHref,
} from './htmlPolicy.js'

export type MarkdownRenderOptions = {
  /** 允许渲染清洗后的 HTML（酒馆卡开关） */
  htmlEnabled: boolean
  /** 允许加载外部图片（默认关） */
  externalImages: boolean
  /** 外链图片经服务端代理取回（需管理员开启且权限达标，默认关） */
  imageProxy: boolean
  /** 整页 HTML + JS 应用放进沙箱 iframe 运行（默认关） */
  jsSandbox: boolean
}

const DEFAULT_OPTIONS: MarkdownRenderOptions = {
  htmlEnabled: false,
  externalImages: false,
  imageProxy: false,
  jsSandbox: false,
}

export const MarkdownRenderOptionsContext =
  createContext<MarkdownRenderOptions>(DEFAULT_OPTIONS)

export const useMarkdownRenderOptions = () => useContext(MarkdownRenderOptionsContext)

/** 单段 HTML 上限；超限降级为纯文本 */
export const MAX_HTML_LENGTH = 256 * 1024

const ALLOWED_TAGS = [
  'details',
  'summary',
  'div',
  'span',
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'th',
  'td',
  'caption',
  'colgroup',
  'col',
  'ul',
  'ol',
  'li',
  'p',
  'b',
  'i',
  'em',
  'strong',
  'u',
  's',
  'del',
  'ins',
  'small',
  'sub',
  'sup',
  'mark',
  'abbr',
  'kbd',
  'samp',
  'var',
  'br',
  'hr',
  'center',
  'img',
  'a',
  'code',
  'pre',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
]

const ALLOWED_ATTR = [
  'class',
  'style',
  'title',
  'alt',
  'src',
  'href',
  'width',
  'height',
  'colspan',
  'rowspan',
  'span',
  'align',
  'valign',
  'open',
  'start',
]

const PURIFY_CONFIG = {
  ALLOWED_TAGS,
  ALLOWED_ATTR,
  ALLOWED_NAMESPACES: ['http://www.w3.org/1999/xhtml'],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
}

/** 站内相对路径经 apiBase 解析；其它原样返回 */
const resolveSrc = (src: string): string =>
  src.startsWith('/') && !src.startsWith('//') ? assetUrl(src) : src

/**
 * 卡内脚本通常在 data-name 为空或占位值时隐藏对应块（如未出场的观察对象）。
 * 我们不执行卡内 JavaScript，这里在清洗前做等价的原生剪枝。
 * 只用惰性 `<template>` 解析，不会触发脚本或网络请求。
 */
const EMPTY_SLOT_VALUES = new Set(['', '暂无', '无', 'null', 'undefined'])

const pruneEmptySlots = (value: string): string => {
  if (!value.includes('data-name')) {
    return value
  }
  const template = document.createElement('template')
  template.innerHTML = value
  let changed = false
  template.content.querySelectorAll('[data-name]').forEach((el) => {
    const name = (el.getAttribute('data-name') || '').trim().toLowerCase()
    if (EMPTY_SLOT_VALUES.has(name)) {
      el.remove()
      changed = true
    }
  })
  return changed ? template.innerHTML : value
}

/**
 * 清洗一段 HTML。返回可直接写入 dangerouslySetInnerHTML 的字符串；
 * 超限或清洗后为空返回 ''。
 */
export const sanitizeStHtml = (
  value: string,
  options: MarkdownRenderOptions
): string => {
  if (!value || value.length > MAX_HTML_LENGTH) {
    return ''
  }
  const cleaned = DOMPurify.sanitize(pruneEmptySlots(value), PURIFY_CONFIG)
  if (!cleaned) {
    return ''
  }
  const template = document.createElement('template')
  template.innerHTML = cleaned
  template.content.querySelectorAll('*').forEach((el) => {
    const tag = el.tagName.toLowerCase()

    const style = filterSafeStyle(el.getAttribute('style'))
    if (style) {
      el.setAttribute('style', style)
    } else {
      el.removeAttribute('style')
    }
    const className = filterSafeClasses(el.getAttribute('class'))
    if (className) {
      el.setAttribute('class', className)
    } else {
      el.removeAttribute('class')
    }

    if (tag === 'a') {
      const href = safeLinkHref(el.getAttribute('href') || '')
      if (href) {
        el.setAttribute('href', href)
        el.setAttribute('target', '_blank')
        el.setAttribute('rel', 'noopener noreferrer')
      } else {
        el.removeAttribute('href')
      }
      return
    }

    if (tag === 'img') {
      const src = safeImageSrc(el.getAttribute('src') || '', {
        external: options.externalImages,
      })
      if (!src) {
        // 外链图片关闭或地址不合法：用 alt 文本占位，绝不发起请求
        el.replaceWith(document.createTextNode((el.getAttribute('alt') || '').trim()))
        return
      }
      el.setAttribute('loading', 'lazy')
      el.setAttribute('referrerpolicy', 'no-referrer')
      if (options.imageProxy && isExternalImage(src)) {
        // 代理模式：不写 src，挂载后由组件 fetch 取回 blob: URL 再回填，
        // 避免浏览器直连外链泄露本机地址
        el.setAttribute('data-st-proxy-src', src)
        el.removeAttribute('src')
        return
      }
      el.setAttribute('src', resolveSrc(src))
    }
  })
  return template.innerHTML
}

/**
 * 渲染一段将被 markdown 解析为 `html` 节点的 HTML。
 * 点击内联图片复用全屏看图器。
 */
export const SanitizedHtml = memo(function SanitizedHtml({
  value,
  inline = false,
}: {
  value: string
  inline?: boolean
}) {
  const options = useMarkdownRenderOptions()
  const { htmlEnabled, externalImages, imageProxy } = options
  const html = useMemo(
    () =>
      htmlEnabled
        ? sanitizeStHtml(value, { htmlEnabled, externalImages, imageProxy, jsSandbox: false })
        : '',
    [value, htmlEnabled, externalImages, imageProxy]
  )
  const [viewerImage, setViewerImage] = useState<ViewerImage | null>(null)
  const hostRef = useRef<HTMLElement | null>(null)

  // 代理模式：清洗结果只带 data-st-proxy-src，挂载后逐个取回 blob: URL。
  useEffect(() => {
    const host = hostRef.current
    if (!html || !imageProxy || !host) {
      return undefined
    }
    let cancelled = false
    host.querySelectorAll<HTMLImageElement>('img[data-st-proxy-src]').forEach((img) => {
      const remote = img.getAttribute('data-st-proxy-src')
      if (!remote) {
        return
      }
      fetchProxyImage(remote)
        .then((blobUrl) => {
          if (!cancelled) {
            img.setAttribute('src', blobUrl)
          }
        })
        .catch(() => {
          // 代理失败：保留 alt 文本占位，不回落到直连外链
        })
    })
    return () => {
      cancelled = true
    }
  }, [html, imageProxy])

  if (!htmlEnabled) {
    // 开关关闭：保持此前的转义文本行为
    return <span className="ds-markdown-html">{value}</span>
  }
  if (!html) {
    return null
  }

  const onClick = (event: ReactMouseEvent<HTMLElement>) => {
    const target = event.target as HTMLElement | null
    const img = target?.closest?.('img')
    const src = img?.getAttribute('src')
    if (src) {
      event.preventDefault()
      setViewerImage({ src })
    }
  }

  return (
    <>
      {inline ? (
        <span
          ref={(node) => {
            hostRef.current = node
          }}
          className="ds-markdown-html"
          dangerouslySetInnerHTML={{ __html: html }}
          onClick={onClick}
        />
      ) : (
        <div
          ref={(node) => {
            hostRef.current = node
          }}
          className="ds-markdown-html ds-markdown-html-block"
          dangerouslySetInnerHTML={{ __html: html }}
          onClick={onClick}
        />
      )}
      {viewerImage ? (
        <ImageViewer image={viewerImage} onClose={() => setViewerImage(null)} />
      ) : null}
    </>
  )
})
