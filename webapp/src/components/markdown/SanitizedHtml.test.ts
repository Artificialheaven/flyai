// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { MAX_HTML_LENGTH, sanitizeStHtml, type MarkdownRenderOptions } from './SanitizedHtml.js'

const ON: MarkdownRenderOptions = {
  htmlEnabled: true,
  externalImages: false,
  imageProxy: false,
  jsSandbox: false,
}

const parse = (html: string): DocumentFragment => {
  const template = document.createElement('template')
  template.innerHTML = html
  return template.content
}

describe('sanitizeStHtml', () => {
  it('移除脚本与事件属性', () => {
    const out = sanitizeStHtml('<script>alert(1)</script><p onclick="alert(2)">hi</p>', ON)
    expect(out).not.toContain('script')
    expect(out).not.toContain('onclick')
    expect(parse(out).querySelector('p')?.textContent).toBe('hi')
  })

  it('保留状态面板常用的 details/table 结构', () => {
    const out = sanitizeStHtml(
      '<details open><summary>状态</summary><table><tr><th>体力</th><td>12</td></tr></table></details>',
      ON
    )
    const fragment = parse(out)
    expect(fragment.querySelector('details')?.hasAttribute('open')).toBe(true)
    expect(fragment.querySelector('summary')?.textContent).toBe('状态')
    expect(fragment.querySelector('td')?.textContent).toBe('12')
  })

  it('链接按白名单收口并加 noopener', () => {
    const fragment = parse(
      sanitizeStHtml('<a href="javascript:alert(1)">坏</a><a href="https://example.com">好</a>', ON)
    )
    const [bad, good] = Array.from(fragment.querySelectorAll('a'))
    expect(bad.hasAttribute('href')).toBe(false)
    expect(good.getAttribute('href')).toBe('https://example.com')
    expect(good.getAttribute('target')).toBe('_blank')
    expect(good.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('关闭外链图片时降级为 alt 文本', () => {
    const out = sanitizeStHtml(
      '<img src="https://cdn.example.com/a.png" alt="场景">',
      ON
    )
    expect(out).not.toContain('<img')
    expect(parse(out).textContent).toBe('场景')
  })

  it('开启外链图片时保留并加懒加载/无 referrer', () => {
    const fragment = parse(
      sanitizeStHtml('<img src="https://cdn.example.com/a.png" alt="场景">', {
        ...ON,
        externalImages: true,
      })
    )
    const img = fragment.querySelector('img')
    expect(img?.getAttribute('src')).toBe('https://cdn.example.com/a.png')
    expect(img?.getAttribute('loading')).toBe('lazy')
    expect(img?.getAttribute('referrerpolicy')).toBe('no-referrer')
  })

  it('代理模式只写 data-st-proxy-src，不直连外链', () => {
    const fragment = parse(
      sanitizeStHtml('<img src="https://cdn.example.com/a.png" alt="场景">', {
        ...ON,
        externalImages: true,
        imageProxy: true,
      })
    )
    const img = fragment.querySelector('img')
    expect(img?.getAttribute('data-st-proxy-src')).toBe('https://cdn.example.com/a.png')
    expect(img?.hasAttribute('src')).toBe(false)
  })

  it('style 与 class 走白名单', () => {
    const fragment = parse(
      sanitizeStHtml('<div class="foo safe-x" style="color: red; position: fixed">x</div>', ON)
    )
    const div = fragment.querySelector('div')
    expect(div?.getAttribute('class')).toBe('safe-x')
    expect(div?.getAttribute('style')).toBe('color: red')
  })

  it('超限或空内容返回空串', () => {
    expect(sanitizeStHtml('', ON)).toBe('')
    expect(sanitizeStHtml(`<p>${'x'.repeat(MAX_HTML_LENGTH)}</p>`, ON)).toBe('')
  })

  it('清洗前剪掉 data-name 为空或占位值的面板块', () => {
    const fragment = parse(
      sanitizeStHtml(
        '<div data-name="">空</div>' +
          '<div data-name=" 暂无 ">暂无</div>' +
          '<div data-name="无">无</div>' +
          '<div data-name="null">null</div>' +
          '<div data-name="undefined">undefined</div>' +
          '<div data-name="糯米天师">有</div>',
        ON
      )
    )
    const blocks = Array.from(fragment.querySelectorAll('div'))
    expect(blocks.map((el) => el.textContent)).toEqual(['有'])
    expect(blocks[0].hasAttribute('data-name')).toBe(false)
  })
})
