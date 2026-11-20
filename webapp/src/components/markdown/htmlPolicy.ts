/**
 * 酒馆卡 HTML 内联展示的 URL / 样式白名单（纯函数，便于单测）。
 *
 * 这里的判断只做「缩小」：DOMPurify 负责移除危险标签与事件属性，
 * 本模块再对链接、图片地址、style/class 做二次收紧。
 */

const CONTROL_CHARS = /[\u0000-\u001f\u007f]/
const SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/
const SAFE_LINK_PROTOCOLS = ['http:', 'https:', 'mailto:']
const SAFE_CLASS_RE = /^safe-[a-z0-9_-]+$/i

/** 允许写进 HTML 的 CSS 属性；不含 position/z-index 等可覆盖布局的属性 */
const ALLOWED_STYLE_PROPS = new Set([
  'color',
  'background',
  'background-color',
  'border',
  'border-top',
  'border-right',
  'border-bottom',
  'border-left',
  'border-color',
  'border-style',
  'border-width',
  'border-radius',
  'border-collapse',
  'border-spacing',
  'padding',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'margin',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'width',
  'height',
  'min-width',
  'max-width',
  'min-height',
  'max-height',
  'display',
  'flex',
  'flex-direction',
  'flex-wrap',
  'flex-grow',
  'flex-shrink',
  'flex-basis',
  'justify-content',
  'align-items',
  'align-self',
  'gap',
  'row-gap',
  'column-gap',
  'grid-template-columns',
  'grid-template-rows',
  'grid-auto-flow',
  'grid-column',
  'grid-row',
  'overflow',
  'overflow-x',
  'overflow-y',
  'text-align',
  'text-decoration',
  'text-decoration-color',
  'text-decoration-line',
  'text-decoration-style',
  'text-shadow',
  'text-transform',
  'text-indent',
  'text-overflow',
  'font',
  'font-size',
  'font-weight',
  'font-style',
  'font-family',
  'font-variant',
  'line-height',
  'letter-spacing',
  'word-spacing',
  'word-break',
  'word-wrap',
  'overflow-wrap',
  'white-space',
  'vertical-align',
  'opacity',
  'box-shadow',
  'box-sizing',
  'list-style',
  'list-style-type',
  'list-style-position',
  'table-layout',
  'caption-side',
  'float',
  'clear',
  'outline',
  'visibility',
  'user-select',
  'writing-mode',
])

/** CSS 值里出现这些片段即整条丢弃（外链、脚本、变量间接引用、CSS 转义） */
const FORBIDDEN_STYLE_VALUE =
  /url\s*\(|expression|behaviou?r|binding|javascript:|@import|var\s*\(|image-set|\\|[<>]/

/** 链接：http/https/mailto、站内路径、相对路径；其余返回空串。 */
export const safeLinkHref = (raw: string): string => {
  const url = (raw || '').trim()
  if (!url || CONTROL_CHARS.test(url)) {
    return ''
  }
  // 页内锚点与站内绝对路径
  if (url.startsWith('#') || url.startsWith('/')) {
    return url
  }
  if (!SCHEME_RE.test(url)) {
    // 无协议：视为相对路径（浏览器按当前站点解析）
    return url
  }
  try {
    const parsed = new URL(url)
    return SAFE_LINK_PROTOCOLS.includes(parsed.protocol) ? url : ''
  } catch {
    return ''
  }
}

/** 图片地址是否指向站外（http/https 或协议相对）。 */
export const isExternalImage = (raw: string): boolean => {
  const url = (raw || '').trim()
  if (url.startsWith('//')) {
    return true
  }
  const match = SCHEME_RE.exec(url)
  if (!match) {
    return false
  }
  const scheme = match[0].toLowerCase()
  return scheme === 'http:' || scheme === 'https:'
}

/**
 * 图片 src 策略：只允许站内相对路径（/uploads、/static、相对路径）与
 * 开启外部图片时的 http/https；data:/blob:/javascript: 一律拒绝。
 */
export const safeImageSrc = (
  raw: string,
  options: { external: boolean }
): string => {
  const url = (raw || '').trim()
  if (!url || CONTROL_CHARS.test(url)) {
    return ''
  }
  if (url.startsWith('//')) {
    return options.external ? url : ''
  }
  const match = SCHEME_RE.exec(url)
  if (!match) {
    // 无协议：相对路径
    return url
  }
  const scheme = match[0].toLowerCase()
  if (scheme !== 'http:' && scheme !== 'https:') {
    return ''
  }
  if (!options.external) {
    // 同源绝对地址在关闭外链图片时仍可显示
    try {
      return new URL(url).origin === window.location.origin ? url : ''
    } catch {
      return ''
    }
  }
  return url
}

/** 只保留 allowlist 内的 CSS 声明；其余整条丢弃。 */
export const filterSafeStyle = (style: string | null | undefined): string => {
  if (!style) {
    return ''
  }
  const kept: string[] = []
  for (const declaration of style.split(';')) {
    const colon = declaration.indexOf(':')
    if (colon <= 0) {
      continue
    }
    const prop = declaration.slice(0, colon).trim().toLowerCase()
    const value = declaration.slice(colon + 1).trim()
    if (!ALLOWED_STYLE_PROPS.has(prop) || !value || value.length > 300) {
      continue
    }
    if (FORBIDDEN_STYLE_VALUE.test(value.toLowerCase())) {
      continue
    }
    kept.push(`${prop}: ${value}`)
  }
  return kept.join('; ')
}

/** class 只保留 safe-* 前缀的自定义类名。 */
export const filterSafeClasses = (value: string | null | undefined): string => {
  if (!value) {
    return ''
  }
  return value
    .split(/\s+/)
    .filter((name) => SAFE_CLASS_RE.test(name))
    .join(' ')
}
