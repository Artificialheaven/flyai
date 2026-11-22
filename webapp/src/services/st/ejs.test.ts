import { describe, expect, it } from 'vitest'
import { EJS_RUNTIME_SOURCE, renderEjs, scanEjs } from './ejs.js'

describe('EJS 子集', () => {
  it('文本、语句与转义输出', () => {
    const out = renderEjs('你好 <%= name %>\n<% if (ok) { %>通过<% } %>', {
      name: '<苏晚晴>',
      ok: true,
    })
    expect(out).toBe('你好 &lt;苏晚晴&gt;\n通过')
  })

  it('<%- 原样输出', () => {
    expect(renderEjs('<%- html %>', { html: '<b>x</b>' })).toBe('<b>x</b>')
  })

  it('注释与转义区', () => {
    expect(renderEjs('a<%# 注释 %>b', {})).toBe('ab')
    expect(renderEjs('a<#escape-ejs><% not a tag %></#escape-ejs>b', {})).toBe(
      'a<% not a tag %>b'
    )
  })

  it('空白裁剪', () => {
    expect(renderEjs('a   <%_ var x = 1; %>\nb', {})).toBe('a\nb')
    expect(renderEjs('a\n<%_ var x = 1; -%>\nb', {})).toBe('ab')
    expect(renderEjs('a<% var x = 1; _%>   \nb', {})).toBe('ab')
  })

  it('循环与字符串里的 %> 不会截断标签', () => {
    const out = renderEjs(
      '<% const sep = "%>"; for (const n of list) { %><%= n %><%- sep %><% } %>',
      { list: ['甲', '乙'] }
    )
    expect(out).toBe('甲%>乙%>')
  })

  it('getvar / setvar / variables', () => {
    const store: Record<string, number> = { hp: 3 }
    const variables = { hp: 3 }
    const context = {
      variables,
      getvar: (name: string) => store[name],
      setvar: (name: string, value: number) => {
        store[name] = value
        return value
      },
    }
    const out = renderEjs(
      '<%= getvar("hp") %><% setvar("hp", getvar("hp") + 1) %><%= variables.hp %>',
      context
    )
    // variables 是进入模板时的快照；setvar 后读取它仍是旧值
    expect(out).toBe('33')
    expect(store.hp).toBe(4)
  })

  it('未闭合标记报错', () => {
    expect(() => renderEjs('a<% if (x) {', {})).toThrow()
  })

  it('嵌入沙箱的运行时源码可独立执行', () => {
    const factory = new Function(`return ${EJS_RUNTIME_SOURCE}`)() as {
      render: (template: string, context: Record<string, unknown>) => string
    }
    expect(factory.render('你好 <%= name %>', { name: '小猫' })).toBe('你好 小猫')
    expect(
      factory.render('<% for (let i = 0; i < 2; i += 1) { %><%= i %><% } %>', {})
    ).toBe('01')
  })

  it('scanEjs 区分输出类型', () => {
    const segments = scanEjs('a<%= x %>b<%- y %>c<% z %>d')
    expect(segments.map((segment) => segment.kind)).toEqual([
      'text',
      'escaped',
      'text',
      'raw',
      'text',
      'code',
      'text',
    ])
  })
})
