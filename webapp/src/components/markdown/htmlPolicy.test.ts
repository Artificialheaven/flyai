// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import {
  filterSafeClasses,
  filterSafeStyle,
  isExternalImage,
  safeImageSrc,
  safeLinkHref,
} from './htmlPolicy.js'

describe('safeLinkHref', () => {
  it('允许 http/https/mailto、站内路径与页内锚点', () => {
    expect(safeLinkHref('https://example.com/a')).toBe('https://example.com/a')
    expect(safeLinkHref('http://example.com')).toBe('http://example.com')
    expect(safeLinkHref('mailto:a@b.c')).toBe('mailto:a@b.c')
    expect(safeLinkHref('/uploads/a.png')).toBe('/uploads/a.png')
    expect(safeLinkHref('#top')).toBe('#top')
    expect(safeLinkHref('relative/page')).toBe('relative/page')
  })

  it('拒绝脚本/数据协议与控制字符', () => {
    expect(safeLinkHref('javascript:alert(1)')).toBe('')
    expect(safeLinkHref('data:text/html,x')).toBe('')
    expect(safeLinkHref('vbscript:x')).toBe('')
    expect(safeLinkHref('https://example.com/\u0000x')).toBe('')
    expect(safeLinkHref('')).toBe('')
  })
})

describe('isExternalImage', () => {
  it('识别站外地址', () => {
    expect(isExternalImage('https://cdn.example.com/a.png')).toBe(true)
    expect(isExternalImage('http://cdn.example.com/a.png')).toBe(true)
    expect(isExternalImage('//cdn.example.com/a.png')).toBe(true)
  })

  it('站内与相对路径不算外链', () => {
    expect(isExternalImage('/uploads/a.png')).toBe(false)
    expect(isExternalImage('a.png')).toBe(false)
    expect(isExternalImage('data:image/png;base64,xx')).toBe(false)
  })
})

describe('safeImageSrc', () => {
  const origin = window.location.origin

  it('关闭外链时只允许相对路径与同源地址', () => {
    expect(safeImageSrc('/uploads/a.png', { external: false })).toBe('/uploads/a.png')
    expect(safeImageSrc('a.png', { external: false })).toBe('a.png')
    expect(safeImageSrc(`${origin}/a.png`, { external: false })).toBe(`${origin}/a.png`)
    expect(safeImageSrc('https://cdn.example.com/a.png', { external: false })).toBe('')
    expect(safeImageSrc('//cdn.example.com/a.png', { external: false })).toBe('')
  })

  it('开启外链时允许 http/https 与协议相对地址', () => {
    expect(safeImageSrc('https://cdn.example.com/a.png', { external: true })).toBe(
      'https://cdn.example.com/a.png'
    )
    expect(safeImageSrc('//cdn.example.com/a.png', { external: true })).toBe(
      '//cdn.example.com/a.png'
    )
  })

  it('始终拒绝 data:/blob:/javascript:', () => {
    for (const url of ['data:image/png;base64,xx', 'blob:http://x/y', 'javascript:alert(1)']) {
      expect(safeImageSrc(url, { external: true })).toBe('')
    }
  })
})

describe('filterSafeStyle', () => {
  it('只保留白名单属性', () => {
    expect(filterSafeStyle('color: red; position: fixed; width: 10px')).toBe(
      'color: red; width: 10px'
    )
    expect(filterSafeStyle('display:flex')).toBe('display: flex')
  })

  it('丢弃外链、表达式与变量引用', () => {
    expect(filterSafeStyle('background: url(https://x/a.png)')).toBe('')
    expect(filterSafeStyle('width: expression(alert(1))')).toBe('')
    expect(filterSafeStyle('color: var(--x)')).toBe('')
    expect(filterSafeStyle('color: javascript:x')).toBe('')
  })

  it('丢弃超长值、空值与格式错误声明', () => {
    expect(filterSafeStyle(`color: ${'x'.repeat(301)}`)).toBe('')
    expect(filterSafeStyle('color:')).toBe('')
    expect(filterSafeStyle('not-a-declaration')).toBe('')
    expect(filterSafeStyle(undefined)).toBe('')
  })
})

describe('filterSafeClasses', () => {
  it('只保留 safe-* 类名', () => {
    expect(filterSafeClasses('safe-panel foo safe-bar_baz')).toBe('safe-panel safe-bar_baz')
    expect(filterSafeClasses('foo bar')).toBe('')
    expect(filterSafeClasses(null)).toBe('')
  })
})
