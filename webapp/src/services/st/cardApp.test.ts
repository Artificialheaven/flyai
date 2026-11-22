import { describe, expect, it } from 'vitest'
import type { RegexScript } from '../flyai/types.js'
import {
  APP_END,
  APP_PLACEHOLDER_PREFIX,
  APP_START,
  MAX_CARD_APP_LENGTH,
  MAX_CARD_APP_VARIABLE_DEPTH,
  MAX_CARD_APP_VARIABLES_LENGTH,
  buildCardAppDoc,
  contentSecurityPolicy,
  escapeForScript,
  extractCardApps,
  isCardAppReplacement,
  needsJQuery,
  needsLodash,
  sanitizeCardAppVariables,
  stripAppFence,
  stripJQueryTags,
  stripLodashTags,
} from './cardApp.js'
import LODASH_SOURCE from './vendor/lodash-4.18.1.min.js?raw'
import { applyDisplayScripts } from './regex.js'

const APP =
  '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' +
  '<div id="app"></div><script>document.getElementById("app").textContent = "ok"</script>' +
  '</body></html>'

describe('isCardAppReplacement', () => {
  it('整页 HTML 且带脚本才算应用', () => {
    expect(isCardAppReplacement(APP)).toBe(true)
    expect(isCardAppReplacement('<html><body><p>hi</p></body></html>')).toBe(false)
    expect(isCardAppReplacement('<div onclick="x()">hi</div>')).toBe(false)
  })

  it('超过上限不再当作应用', () => {
    const big = APP + ' '.repeat(MAX_CARD_APP_LENGTH)
    expect(isCardAppReplacement(big)).toBe(false)
  })

  it('900KB 的整页应用仍可进入沙箱', () => {
    // 实际卡（约会大作战）的开场建档页约 903KB，旧 512KB 上限会退化成代码块
    const big = APP + ' '.repeat(900 * 1024)
    expect(isCardAppReplacement(big)).toBe(true)
  })
})

describe('stripAppFence', () => {
  it('去掉整页 HTML 外的代码围栏', () => {
    expect(stripAppFence('```html\n' + APP + '\n```')).toBe(APP)
    expect(stripAppFence(APP)).toBe(APP)
  })
})

describe('extractCardApps', () => {
  it('没有哨兵时原样返回', () => {
    const text = 'hello\n\nworld'
    expect(extractCardApps(text)).toEqual({ text, apps: [] })
  })

  it('把哨兵内容抽成占位符', () => {
    const text = `前文\n\n${APP_START}\`\`\`html\n${APP}\n\`\`\`${APP_END}\n\n后文`
    const out = extractCardApps(text)
    expect(out.apps).toEqual([APP])
    expect(out.text).toContain(`${APP_PLACEHOLDER_PREFIX}0@@`)
    expect(out.text).not.toContain(APP_START)
    expect(out.text).toContain('前文')
    expect(out.text).toContain('后文')
  })

  it('多个应用按出现顺序编号', () => {
    const text = `${APP_START}${APP}${APP_END}\n\n${APP_START}${APP}${APP_END}`
    const out = extractCardApps(text)
    expect(out.apps).toHaveLength(2)
    expect(out.text).toContain(`${APP_PLACEHOLDER_PREFIX}0@@`)
    expect(out.text).toContain(`${APP_PLACEHOLDER_PREFIX}1@@`)
  })

  it('哨兵里不是整页应用时保留原文', () => {
    const text = `${APP_START}<div>静态</div>${APP_END}`
    const out = extractCardApps(text)
    expect(out.apps).toEqual([])
    expect(out.text).toContain('<div>静态</div>')
    expect(out.text).not.toContain(APP_PLACEHOLDER_PREFIX)
  })

  it('哨兵不完整时保留原文', () => {
    const text = `前文${APP_START}${APP}`
    const out = extractCardApps(text)
    expect(out.apps).toEqual([])
    expect(out.text).toContain(APP)
  })
})

describe('escapeForScript', () => {
  it('转义 < > 与行分隔符', () => {
    const out = escapeForScript('</script>\u2028\u2029')
    expect(out).not.toContain('</script>')
    expect(out).toContain('\\u003c')
    expect(out).toContain('\\u2028')
    expect(out).toContain('\\u2029')
  })
})

describe('contentSecurityPolicy', () => {
  it('默认禁止一切网络', () => {
    const csp = contentSecurityPolicy(false)
    expect(csp).toContain("default-src 'none'")
    expect(csp).toContain("connect-src 'none'")
    expect(csp).toContain("frame-src 'none'")
    expect(csp).toContain('img-src data: blob:;')
    expect(csp).toContain('media-src data: blob:;')
    // 单文件卡把主逻辑内嵌为 data:text/javascript
    expect(csp).toContain("script-src 'unsafe-inline' 'unsafe-eval' data:")
  })

  it('开启外链插图后放行 https 媒体', () => {
    const csp = contentSecurityPolicy(true)
    expect(csp).toContain('img-src data: blob: https:')
    expect(csp).toContain('media-src data: blob: https:')
  })

  it('角色头像源单独加入 img-src，且只接受 http(s)', () => {
    const csp = contentSecurityPolicy(false, [
      'https://cdn.example.com/avatar/a.png',
      'javascript:alert(1)',
      'not a url',
      'https://cdn.example.com/other.png',
    ])
    expect(csp).toContain('img-src data: blob: https://cdn.example.com;')
    expect(csp).not.toContain('javascript:')
    expect(csp).toContain("connect-src 'none'")
    // 头像源只影响图片，媒体仍按开关
    expect(csp).toContain('media-src data: blob:;')
  })
})

describe('内置 jQuery', () => {
  const JQ_TAG = '<script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>'

  it('检测 jQuery 标签或 jQuery/$ 用法', () => {
    expect(needsJQuery(`<html><head>${JQ_TAG}</head></html>`)).toBe(true)
    expect(needsJQuery('<script>$(function(){})</script>')).toBe(true)
    expect(needsJQuery('<script>jQuery.noop()</script>')).toBe(true)
    expect(needsJQuery(APP)).toBe(false)
  })

  it('移除卡片自带 jQuery 标签，保留其它脚本', () => {
    const html = `<head>${JQ_TAG}<script src="https://cdn.jsdelivr.net/x.js"></script></head>`
    const out = stripJQueryTags(html)
    expect(out).not.toContain('code.jquery.com/jquery-3.6.0.min.js')
    expect(out).toContain('cdn.jsdelivr.net/x.js')
  })

  it('需要时内联内置 jQuery，且先于 shim 与卡内脚本', () => {
    const html = APP.replace(
      '<meta charset="utf-8">',
      `<meta charset="utf-8">${JQ_TAG}`
    )
    const doc = buildCardAppDoc(html, { message: '' })
    expect(doc).not.toContain('code.jquery.com/jquery-3.6.0.min.js')
    expect(doc).toContain('jQuery v3.6.0')
    expect(doc.indexOf('jQuery v3.6.0')).toBeLessThan(doc.indexOf('flyai-card-resize'))
    expect(doc.indexOf('jQuery v3.6.0')).toBeLessThan(doc.indexOf('textContent = "ok"'))
    // 内联脚本没有被 jQuery 源码提前截断：源码本身不含闭合标签
    // （字符串里出现的 <script> 字面量不算），因此只比无 jQuery 版本多一个闭合标签
    const plain = buildCardAppDoc(APP, { message: '' })
    expect(doc.split('</script').length).toBe(plain.split('</script').length + 1)
  })

  it('不引用 jQuery 的卡片不额外注入', () => {
    const doc = buildCardAppDoc(APP, { message: '' })
    expect(doc).not.toContain('jQuery v3.6.0')
  })
})

describe('内置 lodash', () => {
  const LODASH_TAG =
    '<script src="https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.min.js"></script>'
  const LODASH_APP =
    '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' +
    '<script>var t = _.get({ a: { b: 1 } }, "a.b")</script>' +
    '<div id="app"></div></body></html>'

  it('检测 lodash 标签或 _. / lodash. 用法', () => {
    expect(needsLodash(`<html><head>${LODASH_TAG}</head></html>`)).toBe(true)
    expect(
      needsLodash(`<script>const t = _.get(stat, '世界.当前时间')</script>`)
    ).toBe(true)
    expect(needsLodash('<script>_(items).map(fn).value()</script>')).toBe(true)
    expect(needsLodash('<script>lodash.get(stat, "a")</script>')).toBe(true)
    // 普通标识符不算 lodash
    expect(needsLodash('<script>const snake_case = 1; foo_bar()</script>')).toBe(
      false
    )
    expect(needsLodash(APP)).toBe(false)
  })

  it('移除卡片自带 lodash 标签，保留其它脚本', () => {
    const html = `<head>${LODASH_TAG}<script src="https://cdn.jsdelivr.net/x.js"></script></head>`
    const out = stripLodashTags(html)
    expect(out).not.toContain('lodash.min.js')
    expect(out).toContain('cdn.jsdelivr.net/x.js')
  })

  it('需要时内联内置 lodash，且先于 shim 与卡内脚本', () => {
    const html = LODASH_APP.replace(
      '<meta charset="utf-8">',
      `<meta charset="utf-8">${LODASH_TAG}`
    )
    const doc = buildCardAppDoc(html, { message: '' })
    expect(doc).not.toContain('lodash@4.17.21/lodash.min.js')
    expect(doc).toContain('__lodashVendor')
    expect(doc).toContain('window._ = __lodashVendor')
    expect(doc.indexOf('__lodashVendor')).toBeLessThan(
      doc.indexOf('flyai-card-resize')
    )
    expect(doc.indexOf('__lodashVendor')).toBeLessThan(
      doc.indexOf('var t = _.get')
    )
  })

  it('不引用 lodash 的卡片不额外注入', () => {
    const doc = buildCardAppDoc(APP, { message: '' })
    expect(doc).not.toContain('__lodashVendor')
  })

  it('内置 bundle 可执行，`_.get` 能读 stat_data', () => {
    const factory = new Function(`${LODASH_SOURCE}; return __lodashVendor;`)
    const lodash = factory() as {
      get: (value: unknown, path: string, fallback?: unknown) => unknown
    }
    const stat = { 世界: { 当前时间: '放学后' } }
    expect(lodash.get(stat, '世界.当前时间', '未知')).toBe('放学后')
    expect(lodash.get(stat, '世界.当前地点', '未知')).toBe('未知')
  })
})

describe('sanitizeCardAppVariables', () => {
  it('没有变量时给空 stat_data', () => {
    expect(sanitizeCardAppVariables(undefined)).toEqual({
      variables: { stat_data: {} },
      serialized: '{"stat_data":{}}',
      truncated: false,
    })
    expect(sanitizeCardAppVariables(null).truncated).toBe(false)
  })

  it('完整状态原样保留，直接根对象包进 stat_data', () => {
    const full = { stat_data: { hp: 7 }, display_data: {} }
    expect(sanitizeCardAppVariables(full).variables).toEqual(full)
    expect(sanitizeCardAppVariables({ hp: 7 }).variables).toEqual({
      stat_data: { hp: 7 },
    })
  })

  it('循环引用替换为占位符且不标记截断', () => {
    const root: Record<string, unknown> = { stat_data: {} }
    ;(root.stat_data as Record<string, unknown>).self = root
    const out = sanitizeCardAppVariables(root)
    expect(out.truncated).toBe(false)
    const stat = out.variables.stat_data as Record<string, unknown>
    expect(stat.self).toBe('[Circular]')
  })

  it('超深嵌套截断并标记', () => {
    let node: Record<string, unknown> = { leaf: 1 }
    for (let i = 0; i < MAX_CARD_APP_VARIABLE_DEPTH + 4; i += 1) {
      node = { child: node }
    }
    const out = sanitizeCardAppVariables({ stat_data: node })
    expect(out.truncated).toBe(true)
    expect(out.serialized).toContain('[MaxDepth]')
  })

  it('超长字符串截断并标记', () => {
    const out = sanitizeCardAppVariables({ stat_data: { text: 'x'.repeat(40_000) } })
    expect(out.truncated).toBe(true)
    expect((out.variables.stat_data as Record<string, unknown>).text).toHaveLength(32 * 1024)
  })

  it('序列化超过总上限时退回空状态', () => {
    const big: Record<string, string> = {}
    for (let i = 0; i <= MAX_CARD_APP_VARIABLES_LENGTH / 1000; i += 1) {
      big[`k${i}`] = 'x'.repeat(1000)
    }
    const out = sanitizeCardAppVariables({ stat_data: big })
    expect(out.truncated).toBe(true)
    expect(out.serialized).toBe('{"stat_data":{}}')
  })

  it('不修改输入对象', () => {
    const input = { stat_data: { hp: 7, nested: { ok: true } } }
    const snapshot = JSON.stringify(input)
    sanitizeCardAppVariables(input)
    expect(JSON.stringify(input)).toBe(snapshot)
  })
})

describe('buildCardAppDoc', () => {
  it('在 head 后注入 CSP 与 shim，且 shim 先于卡内脚本', () => {
    const doc = buildCardAppDoc(APP, { message: 'hello' })
    expect(doc).toContain('Content-Security-Policy')
    expect(doc.indexOf('flyai-card-resize')).toBeLessThan(doc.indexOf('id="app"'))
    expect(doc.indexOf('getChatMessages')).toBeLessThan(doc.indexOf('textContent = "ok"'))
  })

  it('缺少 head 时补在 html 之后', () => {
    const doc = buildCardAppDoc('<html><body><script>1</script></body></html>', {
      message: '',
    })
    expect(doc).toContain('<html><head><meta http-equiv="Content-Security-Policy"')
  })

  it('片段补成完整文档', () => {
    const doc = buildCardAppDoc('<div>x</div><script>1</script>', { message: '' })
    expect(doc.startsWith('<!DOCTYPE html>')).toBe(true)
    expect(doc).toContain('<body><div>x</div>')
  })

  it('消息原文安全嵌入且可被 getChatMessages 读取', () => {
    const doc = buildCardAppDoc(APP, { message: '</script><b>x</b>' })
    expect(doc).not.toContain('"</script><b>x</b>"')
    expect(doc).toContain('\\u003c/script\\u003e')
    expect(doc).toContain('window.getChatMessages')
  })

  it('变量快照嵌入 shim，只读 API 与协议事件齐全', () => {
    const doc = buildCardAppDoc(APP, {
      message: '',
      variables: { stat_data: { hp: 7 } },
    })
    expect(doc).toContain('JSON.parse(')
    expect(doc).toContain('hp')
    for (const api of [
      'window.getAllVariables',
      'window.getChatVariable',
      'window.Mvu',
      'mag_variable_update_ended',
      'waitGlobalInitialized',
      'errorCatched',
      'flyai-card-vars',
      'flyai-card-message',
      'flyai-card-ready',
    ]) {
      expect(doc).toContain(api)
    }
  })

  it('变量里的脚本闭合串不会提前结束注入脚本', () => {
    const doc = buildCardAppDoc(APP, {
      message: '',
      variables: { stat_data: { note: '</script>' } },
    })
    expect(doc).toContain('\\u003c/script\\u003e')
    expect(doc).not.toContain('"</script>"')
    expect(doc.indexOf('window.getAllVariables')).toBeLessThan(
      doc.indexOf('textContent = "ok"')
    )
  })

  it('超限变量不嵌入大载荷，退回空状态', () => {
    const stat: Record<string, string> = {}
    for (let i = 0; i <= MAX_CARD_APP_VARIABLES_LENGTH / 1000; i += 1) {
      stat[`k${i}`] = 'Z'.repeat(1000)
    }
    const doc = buildCardAppDoc(APP, { message: '', variables: { stat_data: stat } })
    expect(doc).not.toContain('Z'.repeat(64))
    expect(doc).toContain('JSON.parse("{\\"stat_data\\":{}}")')
  })

  it('外链媒体开关写进 CSP', () => {
    expect(buildCardAppDoc(APP, { message: '', externalMedia: true })).toContain(
      'img-src data: blob: https:'
    )
    expect(buildCardAppDoc(APP, { message: '' })).not.toContain('img-src data: blob: https:')
  })

  it('角色信息进入 shim 与 CSP img-src，错误上报事件齐全', () => {
    const doc = buildCardAppDoc(APP, {
      message: '',
      character: { name: '角色甲', avatar: 'https://cdn.example.com/a.png' },
    })
    expect(doc).toContain('角色甲')
    expect(doc).toContain('https://cdn.example.com/a.png')
    expect(doc).toContain('getCharName')
    expect(doc).toContain('getCharAvatar')
    expect(doc).toContain('img-src data: blob: https://cdn.example.com;')
    expect(doc).toContain('flyai-card-error')

    const plain = buildCardAppDoc(APP, { message: '' })
    expect(plain).toContain('img-src data: blob:;')
    expect(plain).not.toContain('https://cdn.example.com')
  })

  it('高度回调用 body 尺寸，内容收起后能回缩', () => {
    const doc = buildCardAppDoc(APP, { message: '' })
    expect(doc).not.toContain('documentElement.scrollHeight')
    expect(doc).toContain('getBoundingClientRect().height')
  })
})

describe('L4 能力透传', () => {
  it('默认不授予任何能力', () => {
    const doc = buildCardAppDoc(APP, { message: '' })
    expect(doc).toContain('var CAP_ACTIONS = false;')
    expect(doc).toContain('var CAP_WRITE = false;')
    expect(doc).toContain('var CAP_NET = false;')
    // 网络入口包在 if (CAP_NET) 里，默认不安装
    expect(doc).toContain('window.FlyAI.capabilities = {')
    expect(doc.indexOf('if (CAP_NET) {')).toBeLessThan(doc.indexOf('window.fetch ='))
  })

  it('开启后安装动作、写入与网络入口，且仍先于卡内脚本', () => {
    const doc = buildCardAppDoc(APP, {
      message: '',
      capabilities: { actions: true, write: true, net: true },
    })
    expect(doc).toContain('var CAP_ACTIONS = true;')
    expect(doc).toContain('var CAP_WRITE = true;')
    expect(doc).toContain('var CAP_NET = true;')
    expect(doc).toContain('flyai-card-action')
    expect(doc).toContain('flyai-card-write-context')
    expect(doc).toContain('window.fetch =')
    expect(doc).toContain('window.XMLHttpRequest =')
    expect(doc).toContain('window.FlyAI.updateCharacter')
    // shim 整体先于卡片自己的脚本
    expect(doc.indexOf('window.fetch =')).toBeLessThan(doc.indexOf('textContent = "ok"'))
    expect(doc.indexOf('window.FlyAI.updateCharacter')).toBeLessThan(
      doc.indexOf('textContent = "ok"')
    )
  })

  it('网络能力不改变 CSP，卡内仍然无法直连', () => {
    const doc = buildCardAppDoc(APP, {
      message: '',
      capabilities: { actions: false, write: false, net: true },
    })
    expect(doc).toContain("default-src 'none'")
    expect(doc).toContain("connect-src 'none'")
    expect(doc).not.toContain("connect-src 'self'")
    expect(doc).not.toContain('connect-src https:')
  })
})

describe('展示侧正则与沙箱应用的衔接', () => {
  const script: RegexScript = {
    findRegex: '\\[APP\\]',
    replaceString: APP,
    placement: [2],
  }

  it('默认给整页应用包哨兵，抽离后得到占位符', () => {
    const display = applyDisplayScripts('状态：[APP]', [script])
    expect(display).toContain(APP_START)
    const out = extractCardApps(display)
    expect(out.apps).toEqual([APP])
    expect(out.text).toContain(`${APP_PLACEHOLDER_PREFIX}0@@`)
  })

  it('cardApps:false 时不包哨兵（面板走白名单清洗）', () => {
    const display = applyDisplayScripts('状态：[APP]', [script], undefined, undefined, {
      cardApps: false,
    })
    expect(display).not.toContain(APP_START)
    expect(display).toContain(APP)
    expect(extractCardApps(display).apps).toEqual([])
  })
})
