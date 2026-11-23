import vm from 'node:vm'
import { describe, expect, it } from 'vitest'
import type { Message } from '../chat/types.js'
import type { StAuthorPayload } from '../flyai/types.js'
import {
  DAL_PHONE_RUNTIME_SOURCE,
  adaptAuthorSource,
  adaptHtmlScripts,
  authorRuntimeMessages,
  buildAuthorDoc,
} from './authorRuntime.js'

const AUTHOR: StAuthorPayload = {
  enabled: true,
  scripts: [
    {
      id: 'yzdj-main',
      name: '御主地界',
      content:
        "const P = 'https://cdn.jsdelivr.net/gh/MagicalAstrogy/MagVarUpdate@abc/artifact/bundle.js';" +
        "const Z = 'https://cdn.jsdelivr.net/gh/StageDog/tavern_resource@abc/dist/util/mvu_zod.js';" +
        'window.__YZDJ_STATE__ = { ok: true };',
    },
  ],
  character: { name: '御主地界', description: '[[YZDJ-RULES v1]]', avatar: '' },
}

const message = (overrides: Record<string, unknown>): Message =>
  overrides as unknown as Message

describe('adaptAuthorSource', () => {
  it('改写 window.top / window.parent 的跨源访问', () => {
    const source =
      "window.top.addEventListener('message', fn);" +
      "window.parent.SillyTavern.getContext();" +
      'self?.["top"].document;'
    const out = adaptAuthorSource(source)
    expect(out).toContain('window.__FLYAI_HOST__.addEventListener')
    expect(out).toContain('window.__FLYAI_HOST__.SillyTavern')
    expect(out).toContain('window.__FLYAI_HOST__.document')
    expect(out).not.toContain('window.top')
    expect(out).not.toContain('window.parent')
  })

  it('裸 top / parent 也改写，但不动字符串与注释', () => {
    const source =
      "top.addEventListener('message', fn); // window.top in comment\n" +
      "const s = 'window.top'; parent.document;"
    const out = adaptAuthorSource(source)
    expect(out).toContain('window.__FLYAI_HOST__.addEventListener')
    expect(out).toContain('// window.top in comment')
    expect(out).toContain("'window.top'")
    expect(out).toContain('window.__FLYAI_HOST__.document')
  })

  it('脚本自己声明了 top/parent 时不改写裸标识符', () => {
    const source = 'const top = 1; top.addEventListener;'
    expect(adaptAuthorSource(source)).toBe(source)
  })
})

describe('adaptHtmlScripts', () => {
  it('只改写内联脚本，保留 src 引用与其它 HTML', () => {
    const html =
      '<div>window.top</div><script src="https://x/a.js"></script>' +
      "<script>window.top.addEventListener('message', fn)</script>"
    const out = adaptHtmlScripts(html)
    expect(out).toContain('<div>window.top</div>')
    expect(out).toContain('src="https://x/a.js"')
    expect(out).toContain('window.__FLYAI_HOST__.addEventListener')
    expect(out).not.toContain('window.top.addEventListener')
  })
})

describe('authorRuntimeMessages', () => {
  it('只保留 user/assistant 文本与变量快照', () => {
    const messages = authorRuntimeMessages([
      message({ role: 'user', content: '<b>你好</b>', statData: { a: 1 } }),
      message({
        role: 'assistant',
        blocks: [
          { type: 'text', text: '第一段' },
          { type: 'thinking', text: '思考' },
          { type: 'text', text: '第二段' },
        ],
        statData: { b: 2 },
      }),
      message({ role: 'tombstone' }),
    ])
    expect(messages.map((item) => item.role)).toEqual(['user', 'assistant'])
    expect(messages[0].text).toBe('<b>你好</b>')
    expect(messages[1].text).toBe('第一段第二段')
    expect(messages[1].statData).toEqual({ b: 2 })
  })
})

describe('buildAuthorDoc', () => {
  it('没有脚本时返回空串', () => {
    expect(
      buildAuthorDoc({
        html: '<div data-yzdj></div>',
        author: { enabled: true, scripts: [] },
        messages: [],
      })
    ).toBe('')
  })

  it('把面板与消息镜像放进同一个文档，并注入 shim / 脚本', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj><div class="yzdj-boot">未启动</div></div>',
      author: AUTHOR,
      messages: [
        { role: 'user', text: 'hi <script>alert(1)</script>' },
        { role: 'assistant', text: '世界开始' },
      ],
      variables: { 世界: { 天数: 1 } },
      character: AUTHOR.character,
    })
    expect(doc).toContain('id="chat"')
    expect(doc).toContain('mesid="0"')
    expect(doc).toContain('mesid="1"')
    expect(doc).toContain('hi &lt;script&gt;alert(1)&lt;/script&gt;')
    // 面板根挂在最后一条消息里（正文之后）
    expect(doc.indexOf('世界开始')).toBeLessThan(doc.lastIndexOf('data-yzdj'))
    expect(doc).toContain('class="yzdj-boot"')
    expect(doc).toContain('window.SillyTavern')
    expect(doc).toContain('window.Mvu')
    expect(doc).toContain('window.tavern_events')
    expect(doc).toContain('registerMvuSchema')
    expect(doc).toContain('window.__YZDJ_STATE__')
    expect(doc).toContain('window._ = __lodashVendor')
    expect(doc).toContain('Content-Security-Policy')
    expect(doc).toContain("connect-src 'none'")
    expect(doc).toContain('data:text/javascript')
  })

  it('脚本里的 </script> 不会截断文档', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: {
        ...AUTHOR,
        scripts: [{ id: 's', name: 's', content: 'window.x = "</script>"' }],
      },
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    expect(doc).toContain('<\\/script>')
  })

  it('生成的兼容层是可解析的 JavaScript', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: AUTHOR,
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    const scripts = [...doc.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
      (match) => match[1]
    )
    const shim = scripts.find((source) => source.includes('window.triggerSlash'))
    expect(shim).toBeTruthy()
    expect(() => new Function(shim as string)).not.toThrow()
  })

  it('注入 ST 原生宿主面、EJS 与 import map', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: AUTHOR,
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    expect(doc).toContain('__FLYAI_ST_NATIVE__')
    expect(doc).toContain('window.EjsTemplate')
    expect(doc).toContain('"null/script.js"')
    expect(doc).toContain('"null/scripts/world-info.js"')
    expect(doc).toContain('"null/scripts/openai.js"')
    // 原生宿主可用时不安装卡特定覆盖，保留回退路径。
    expect(doc).toContain('window.__FLYAI_NATIVE_HOST__ && window.DALPhoneRuntime')
    const scripts = [...doc.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
      (match) => match[1]
    )
    const snapshot = scripts.find((source) => source.includes('__FLYAI_ST_NATIVE__'))
    const ejs = scripts.find((source) => source.includes('window.EjsTemplate'))
    expect(snapshot).toBeTruthy()
    expect(ejs).toBeTruthy()
    expect(() => new Function(snapshot as string)).not.toThrow()
    expect(() => new Function(ejs as string)).not.toThrow()
  })

  it('$(fn) 的 ready 回调同步执行（变量结构注册早于 MVU ready 事件）', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: AUTHOR,
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    const shim = [...doc.matchAll(/<script>([\s\S]*?)<\/script>/g)]
      .map((match) => match[1])
      .find((source) => source.includes('window.triggerSlash'))
    expect(shim).toBeTruthy()
    expect(shim).toContain('args[0](target)')
    expect(shim).toContain('new Proxy(jq')
  })

  it('注入 TavernHelper / context 扩展 / locks / 静默生成桥', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: AUTHOR,
      messages: [{ role: 'assistant', text: 'hi' }],
      sessionId: 'sid-1',
      userName: '玩家甲',
      canWrite: true,
    })
    expect(doc).toContain('window.TavernHelper')
    expect(doc).toContain('getChatHistoryDetail')
    expect(doc).toContain('replaceVariables')
    expect(doc).toContain('injectPrompts')
    expect(doc).toContain('chatId: CHAT_ID')
    expect(doc).toContain('name1: USER_NAME')
    expect(doc).toContain('getRequestHeaders')
    expect(doc).toContain('loadWorldInfo')
    expect(doc).toContain('saveWorldInfo')
    expect(doc).toContain('navigator.locks')
    expect(doc).toContain('DALPhoneMainline')
    expect(doc).toContain('__flyaiGenerate')
    expect(doc).toContain('/api/backends/chat-completions/generate')
    expect(doc).toContain('sid-1')
    expect(doc).toContain('玩家甲')
  })

  it('把 MVU / mvu_zod 的 CDN import 映射到本地 shim', () => {
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: AUTHOR,
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    expect(doc).toContain('importmap')
    expect(doc).toContain('MagVarUpdate')
    expect(doc).toContain('mvu_zod.js')
    // data: 模块里导出宿主全局（URL 编码后出现在 importmap）
    expect(doc).toContain(encodeURIComponent('export const Mvu = globalThis.Mvu'))
    expect(doc).toContain(encodeURIComponent('globalThis.registerMvuSchema'))
  })

  it('还支持拼接式 URL（CDN 前缀 + SHA 常量 + 固定路径）', () => {
    const sha = '61010dab47bc3a08a1b626320bf7fc8c9573eca4'
    const zodSha = 'dee97e8c3e24e1e75efe21141743e4b5c0af776e'
    const doc = buildAuthorDoc({
      html: '<div data-yzdj></div>',
      author: {
        ...AUTHOR,
        scripts: [{
          id: 'concat',
          name: 'concat',
          content:
            `const MVU_SHA = '${sha}';\n` +
            "const MVU_PATH = 'MagicalAstrogy/MagVarUpdate@' + MVU_SHA + '/artifact/bundle.js';\n" +
            "const urls = ['https://cdn.jsdelivr.net/gh/' + MVU_PATH];\n" +
            `const SHA = '${zodSha}';\n` +
            "const P = 'StageDog/tavern_resource@' + SHA + '/dist/util/mvu_zod.js';\n" +
            "urls.push('https://cdn.jsdelivr.net/gh/' + P);",
        }],
      },
      messages: [{ role: 'assistant', text: 'hi' }],
    })
    expect(doc).toContain(
      `https://cdn.jsdelivr.net/gh/MagicalAstrogy/MagVarUpdate@${sha}/artifact/bundle.js`
    )
    expect(doc).toContain(
      `https://cdn.jsdelivr.net/gh/StageDog/tavern_resource@${zodSha}/dist/util/mvu_zod.js`
    )
  })
})

type PhoneRuntimeLike = {
  prepareFromHost: (args: unknown) => Promise<{ messages: Array<{ role: string; content: string }> }>
  sendPrepared: (prepared: unknown, options: unknown) => Promise<unknown>
  responseText: (value: unknown) => string
  capabilities: { source?: string }
}

const loadPhoneRuntime = () => {
  const sandbox: Record<string, unknown> = {}
  sandbox.window = sandbox
  const context = vm.createContext(sandbox)
  vm.runInContext(DAL_PHONE_RUNTIME_SOURCE, context)
  return sandbox as {
    DALPhoneRuntime: PhoneRuntimeLike
    __flyaiGenerate?: (purpose: string, options: unknown) => Promise<string>
  }
}

describe('DALPhoneRuntime 兼容实现', () => {
  it('按手机预设顺序拼提示，并通过作者模型桥生成', async () => {
    const sandbox = loadPhoneRuntime()
    const calls: Array<{ purpose: string; messages: Array<{ role: string; content: string }> }> = []
    sandbox.__flyaiGenerate = (purpose, options) => {
      calls.push({
        purpose,
        messages: (options as { messages: Array<{ role: string; content: string }> }).messages,
      })
      return Promise.resolve('你好，我在。')
    }
    const runtime = sandbox.DALPhoneRuntime
    expect(runtime.capabilities.source).toBe('flyai-author-bridge')
    const prepared = await runtime.prepareFromHost({
      preset: {
        prompts: [
          { id: 'main', enabled: true, role: 'system', content: '你是 {{char}}，和 {{user}} 聊天。' },
          { id: 'charDescription', enabled: true, role: 'system', source: 'charDescription' },
          { id: 'chatHistory', enabled: true, role: 'system', source: 'chatHistory' },
          { id: 'replyProtocol', enabled: true, role: 'system', content: '格式 <dal_phone_reply>' },
        ],
      },
      contact: { name: '十香', userName: '玩家', description: '十香资料' },
      mainline: [],
      phoneMessages: [{ role: 'user', content: '在吗' }],
      currentInput: '吃了吗',
    })
    expect(prepared.messages.map((m) => m.content)).toEqual([
      '你是 十香，和 玩家 聊天。',
      '十香资料',
      '在吗',
      '吃了吗',
      '格式 <dal_phone_reply>',
    ])
    const raw = await runtime.sendPrepared(prepared, {})
    expect(calls).toHaveLength(1)
    expect(calls[0].purpose).toBe('phone')
    expect(calls[0].messages).toHaveLength(5)
    expect(runtime.responseText(raw)).toBe(
      '<dal_phone_reply><msg>你好，我在。</msg></dal_phone_reply>'
    )
  })

  it('模型已返回 dal_phone_reply 时不再重复包裹', async () => {
    const sandbox = loadPhoneRuntime()
    sandbox.__flyaiGenerate = () =>
      Promise.resolve('<dal_phone_reply><msg>好</msg></dal_phone_reply>')
    const runtime = sandbox.DALPhoneRuntime
    const prepared = await runtime.prepareFromHost({
      preset: null,
      contact: { name: '十香', userName: '玩家' },
      phoneMessages: [],
      currentInput: '在吗',
    })
    const raw = await runtime.sendPrepared(prepared, {})
    expect(runtime.responseText(raw)).toBe(
      '<dal_phone_reply><msg>好</msg></dal_phone_reply>'
    )
  })
})
