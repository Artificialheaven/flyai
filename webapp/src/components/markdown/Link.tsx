import type { Image, Link as LinkNode } from 'mdast'
import { memo, useEffect, useState, type ReactNode } from 'react'
import { assetUrl } from '../../services/flyai/client.js'
import { fetchProxyImage } from '../../services/st/imageProxy.js'
import { ImageViewer } from '../images/ImageViewer.js'
import { isExternalImage, safeImageSrc } from './htmlPolicy.js'
import { useMarkdownRenderOptions } from './SanitizedHtml.js'

const sanitizeUrl = (url: string) => {
  // 如果URL没有协议，添加默认的https协议
  if (url && !url.match(/^[a-zA-Z]+:\/\//) && !url.startsWith('mailto:')) {
    url = `https://${url}`
  }

  try {
    const parsedUrl = new URL(url)
    if (['http:', 'https:', 'mailto:'].includes(parsedUrl.protocol)) {
      return parsedUrl.toString()
    }
    return undefined
  } catch {
    return undefined
  }
}

export const Link = memo(function Link({
  node,
  children,
}: {
  node: LinkNode
  children: ReactNode
}) {
  // 页内锚点显示链接文字。
  if (node.url.startsWith('#')) {
    return children
  }

  // 邮件地址显示链接文字。
  if (node.url.startsWith('mailto:')) {
    return children
  }

  // URL 解析失败时 href 为空，链接文字仍可显示。
  const href = sanitizeUrl(node.url)

  return (
    <a href={href} title={node.title || undefined} target="_blank" rel="noreferrer">
      {children}
    </a>
  )
})

/**
 * 酒馆卡启用 HTML 渲染时，Markdown 图片内联展示并支持点开看图器；
 * 其余情况保持「指向图片地址的链接」的旧行为。
 */
export function ImageAsLink({ node }: { node: Image }) {
  const { htmlEnabled, externalImages, imageProxy } = useMarkdownRenderOptions()
  const [open, setOpen] = useState(false)
  const [proxySrc, setProxySrc] = useState('')
  const safeSrc = htmlEnabled ? safeImageSrc(node.url, { external: externalImages }) : ''
  const proxied = Boolean(imageProxy && safeSrc && isExternalImage(safeSrc))

  // 代理模式：先经服务端取回 blob: URL，绝不直连外链；失败则只留 alt 文本。
  useEffect(() => {
    if (!proxied) {
      setProxySrc('')
      return undefined
    }
    let cancelled = false
    fetchProxyImage(safeSrc)
      .then((blobUrl) => {
        if (!cancelled) {
          setProxySrc(blobUrl)
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [proxied, safeSrc])

  const displaySrc = proxied
    ? proxySrc
    : safeSrc.startsWith('/') && !safeSrc.startsWith('//')
      ? assetUrl(safeSrc)
      : safeSrc
  if (safeSrc) {
    return (
      <>
        <img
          className="ds-markdown-inline-image"
          src={displaySrc || undefined}
          alt={node.alt ?? ''}
          title={node.title ?? undefined}
          loading="lazy"
          referrerPolicy="no-referrer"
          onClick={() => {
            if (displaySrc) {
              setOpen(true)
            }
          }}
        />
        {open && displaySrc ? (
          <ImageViewer image={{ src: displaySrc }} onClose={() => setOpen(false)} />
        ) : null}
      </>
    )
  }
  const url = sanitizeUrl(node.url)
  return (
    <a href={url} title={node.title ?? ''} target="_blank" rel="noreferrer">
      {url}
    </a>
  )
}
