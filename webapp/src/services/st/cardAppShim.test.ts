import vm from 'node:vm'
import { describe, expect, it } from 'vitest'
import { buildCardAppShim, type CardAppCapabilities } from './cardApp.js'

type Posted = {
  type?: unknown
  v?: unknown
  height?: unknown
  level?: unknown
  text?: unknown
  code?: unknown
  id?: unknown
  intent?: Record<string, unknown>
  request?: Record<string, unknown>
  context?: unknown
  metadata?: unknown
  changes?: unknown
  creates?: unknown
  quiet_prompt?: unknown
  ok?: unknown
  error?: unknown
  status?: unknown
  headers?: Record<string, string>
  bodyBase64?: unknown
}
type MessageLike = {
  type?: string
  source?: unknown
  data?: unknown
  error?: unknown
  reason?: unknown
  message?: unknown
  preventDefault?: () => void
}

type CharacterLike = { name?: string; avatar?: string }

const makeSandbox = (
  message = 'hello',
  variables: Record<string, unknown> | null = { stat_data: { hp: 7 } },
  character: CharacterLike | null = null,
  capabilities: CardAppCapabilities | null = null,
  chat: Parameters<typeof buildCardAppShim>[4] = null
) => {
  const posted: Posted[] = []
  const listeners: Record<string, Array<(event: MessageLike) => void>> = {}

  // 极简 DOM：酒馆输入框兼容层会 createElement/appendChild/事件派发
  const makeElement = (tag: string) => {
    const handlers: Record<string, Array<(event: MessageLike) => void>> = {}
    const element = {
      tagName: tag.toUpperCase(),
      id: '',
      type: '',
      value: '',
      textContent: '',
      tabIndex: 0,
      style: { cssText: '' },
      isConnected: true,
      children: [] as unknown[],
      setAttribute: () => undefined,
      appendChild: (child: { isConnected?: boolean } | undefined) => {
        if (child) {
          child.isConnected = true
          element.children.push(child)
        }
      },
      addEventListener: (type: string, fn: (event: MessageLike) => void) => {
        handlers[type] = handlers[type] || []
        handlers[type].push(fn)
      },
      dispatch: (type: string) => {
        for (const fn of handlers[type] || []) {
          fn({ type, preventDefault: () => undefined })
        }
      },
      click: () => element.dispatch('click'),
      focus: () => undefined,
    }
    return element
  }

  const sandbox: Record<string, unknown> = {
    console: { error: () => undefined, log: () => undefined },
    setInterval: () => 1,
    clearInterval: () => undefined,
    setTimeout: () => 1,
    clearTimeout: () => undefined,
    document: {
      readyState: 'complete',
      createElement: (tag: string) => makeElement(tag),
      getElementById: () => null,
      querySelector: () => null,
      body: {
        scrollHeight: 10,
        offsetHeight: 10,
        getBoundingClientRect: () => ({ height: 10 }),
        appendChild: () => undefined,
      },
      addEventListener: () => undefined,
      querySelectorAll: () => [],
    },
    parent: {
      postMessage: (data: Posted) => {
        posted.push(data)
      },
    },
    addEventListener: (type: string, fn: (event: MessageLike) => void) => {
      listeners[type] = listeners[type] || []
      listeners[type].push(fn)
    },
    removeEventListener: () => undefined,
    open: () => undefined,
    // 沙箱 iframe 里可能被浏览器拒绝的 Web API
    navigator: {},
    // 受控网络 shim 用到的 Web API（Node 18+ 全局）
    Response,
    Headers,
    AbortController,
    TextEncoder,
    TextDecoder,
    DOMException,
    Uint8Array,
    Blob,
    atob,
    btoa,
  }
  sandbox.window = sandbox
  // shim 会把 window.parent/top 指回自身（opaque origin 下读取父窗口属性会抛
  // SecurityError）；这里保留宿主窗口的固定引用供用例模拟宿主消息
  const host = sandbox.parent as Record<string, unknown>
  sandbox.host = host

  const context = vm.createContext(sandbox)
  vm.runInContext(
    buildCardAppShim(message, variables, character, capabilities, chat),
    context
  )

  const emit = (type: string, event: MessageLike) => {
    for (const fn of listeners[type] || []) {
      fn(event)
    }
  }
  const emitMessage = (event: MessageLike) => emit('message', event)
  const emitLoad = () => emit('load', {})

  return { sandbox, posted, emit, emitMessage, emitLoad }
}

const toasts = (posted: Posted[]) => posted.filter((item) => item.type === 'flyai-card-toast')

describe('buildCardAppShim', () => {
  it('遮蔽 parent，父窗口探测不再抛 SecurityError', () => {
    const { sandbox, posted } = makeSandbox()
    // 沙箱 iframe 是 opaque origin：读 window.parent 的具名属性会被浏览器判为
    // 跨源访问。卡片常用 `window.parent.getWorldbookNames` 探测酒馆，这里让其落到
    // shim 的同名 API 上。
    //
    // node:vm 下 `window` 是上下文化后的全局代理，与外层 sandbox 对象不是同一引用，
    // 所以这里断言语义而非引用：parent 已不再指向宿主，且能拿到 shim 的同名 API。
    // top 在 node:vm 里也能遮蔽；真实 Chrome 的 window.top 不可配置，defineProperty
    // 会抛 TypeError（见 cardApp.ts 注释），此处只覆盖 vm 下的代码路径。
    expect(sandbox.parent).not.toBe(sandbox.host)
    expect(sandbox.top).not.toBe(sandbox.host)
    const parent = sandbox.parent as Record<string, unknown>
    const top = sandbox.top as Record<string, unknown>
    expect(typeof parent.getWorldbookNames).toBe('function')
    expect(typeof parent.getChatMessages).toBe('function')
    expect(typeof top.getWorldbookNames).toBe('function')
    // 宿主通道在遮蔽前已保存：卡片消息仍能到达宿主
    expect(posted.some((item) => item.type === 'flyai-card-ready')).toBe(true)
  })

  it('就绪时上报 ready 与高度，并暴露变量快照', () => {
    const { sandbox, posted } = makeSandbox('hi', { stat_data: { hp: 7 } })
    expect(posted).toContainEqual({ v: 1, type: 'flyai-card-ready' })
    expect(posted.some((item) => item.type === 'flyai-card-resize' && item.height === 10)).toBe(
      true
    )
    const getAllVariables = sandbox.getAllVariables as () => unknown
    expect(getAllVariables()).toEqual({ stat_data: { hp: 7 } })
    const getChatVariable = sandbox.getChatVariable as (key: string) => unknown
    expect(getChatVariable('hp')).toBe(7)
  })

  it('提供酒馆输入框兼容层：#send_textarea 与 context.generate', () => {
    const { sandbox, posted } = makeSandbox('hi', null, null, {
      actions: true,
      write: false,
      net: false,
    })
    const doc = sandbox.document as {
      getElementById: (id: string) => { value: string } | null
      querySelector: (selector: string) => { value: string } | null
    }
    const area = doc.getElementById('send_textarea')
    expect(area).toBeTruthy()
    // 展示侧脚本（小猫之神/仓鼠之神）两种取法都要命中
    expect(doc.querySelector('#send_textarea')).toBe(area)
    if (area) {
      area.value = '选它'
    }
    const context = (sandbox.SillyTavern as {
      getContext: () => {
        generate: () => Promise<boolean>
        extensionSettings: Record<string, unknown>
      }
    }).getContext()
    context.extensionSettings.Astro = { optionMode: 'append' }
    expect(context.extensionSettings.Astro).toEqual({ optionMode: 'append' })
    // generate 现在返回等待回复落地的 Promise
    const pending = context.generate()
    expect(typeof pending.then).toBe('function')
    const action = posted.find((item) => item.type === 'flyai-card-action')
    expect(action?.intent).toEqual({ kind: 'action', action: 'send', text: '选它' })
  })

  it('兼容层没有动作能力时只提示只读，不发送', async () => {
    const { sandbox, posted } = makeSandbox()
    const area = (sandbox.document as { getElementById: (id: string) => { value: string } })
      .getElementById('send_textarea')
    area.value = '选它'
    const generate = (sandbox.SillyTavern as {
      getContext: () => { generate: () => Promise<boolean> }
    }).getContext().generate
    await expect(generate()).resolves.toBe(false)
    expect(posted.some((item) => item.type === 'flyai-card-action')).toBe(false)
    expect(toasts(posted).map((item) => item.code)).toEqual(['read-only'])
  })

  it('宿主草稿同步到兼容输入框，修改后回传 compose 意图', () => {
    const { sandbox, posted, emitMessage } = makeSandbox()
    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-draft', text: '已有内容' },
    })
    const area = (sandbox.document as {
      getElementById: (id: string) => { value: string; dispatch: (type: string) => void }
    }).getElementById('send_textarea')
    expect(area.value).toBe('已有内容')
    // 展示侧脚本常见写法：改 value 后派发 input
    area.value = '已有内容 追加'
    area.dispatch('input')
    const compose = posted.filter((item) => item.type === 'flyai-card-compose')
    expect(compose.at(-1)?.text).toBe('已有内容 追加')
  })

  it('getVariables 区分 chat/global，重复载荷不重复广播', () => {
    const { sandbox, emitMessage } = makeSandbox()
    const getVariables = sandbox.getVariables as (options?: {
      type?: string
    }) => unknown
    expect(getVariables()).toEqual({ stat_data: { hp: 7 } })
    expect(getVariables({ type: 'global' })).toEqual({})
    expect(getVariables({ type: 'chat' })).toEqual({ stat_data: { hp: 7 } })

    const calls: unknown[] = []
    const eventOn = sandbox.eventOn as (event: string, fn: (value: unknown) => void) => void
    eventOn('mag_variable_update_ended', (value) => calls.push(value))
    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 7 } } },
    })
    expect(calls).toHaveLength(0)
  })

  it('getChatMessages 读取当前消息，message 事件可更新', () => {
    const { sandbox, emitMessage } = makeSandbox('第一条')
    const getChatMessages = sandbox.getChatMessages as (
      id?: unknown
    ) => Array<{ message: string; message_id: number }>
    expect(getChatMessages('12')[0].message).toBe('第一条')
    expect(getChatMessages('12')[0].message_id).toBe(12)

    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-message', message: '第二条' },
    })
    expect(getChatMessages()[0].message).toBe('第二条')
  })

  it('写入 API 只发只读提示，不改变量', () => {
    const { sandbox, posted } = makeSandbox()
    const setChatVariable = sandbox.setChatVariable as () => boolean
    const setGlobalVariable = sandbox.setGlobalVariable as () => boolean
    expect(setChatVariable()).toBe(false)
    expect(setGlobalVariable()).toBe(false)
    expect(toasts(posted).map((item) => item.code)).toEqual(['read-only', 'read-only'])

    const triggerSlash = sandbox.triggerSlash as (command: string) => string
    expect(triggerSlash('/setvar hp 9')).toBe('')
    expect(triggerSlash('/echo hi')).toBe('')
    expect(toasts(posted)).toHaveLength(3)
    const getAllVariables = sandbox.getAllVariables as () => unknown
    expect(getAllVariables()).toEqual({ stat_data: { hp: 7 } })
  })

  it('Mvu 只读：可读快照，写入返回拒绝', async () => {
    const { sandbox, posted } = makeSandbox()
    const Mvu = sandbox.Mvu as {
      getMvuData: () => { stat_data: unknown }
      replaceMvuData: () => Promise<unknown>
      setMvuData: () => boolean
    }
    expect(Mvu.getMvuData().stat_data).toEqual({ hp: 7 })
    expect(Mvu.setMvuData()).toBe(false)
    await expect(Mvu.replaceMvuData()).rejects.toThrow('flyai-card-read-only')
    expect(toasts(posted)).toHaveLength(2)
  })

  it('L2：连续快照更新后变量、Mvu 与事件保持同步', () => {
    const { sandbox, emitMessage } = makeSandbox()
    const calls: unknown[] = []
    const eventOn = sandbox.eventOn as (event: string, fn: (value: unknown) => void) => void
    eventOn('mag_variable_update_ended', (value) => calls.push(value))

    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 9 } } },
    })
    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 12 } } },
    })

    expect((sandbox.getChatVariable as (key: string) => unknown)('hp')).toBe(12)
    expect((sandbox.getAllVariables as () => { stat_data: unknown })().stat_data).toEqual({
      hp: 12,
    })
    expect(
      (sandbox.Mvu as { getMvuData: () => { stat_data: unknown } }).getMvuData().stat_data
    ).toEqual({ hp: 12 })
    expect(calls).toEqual([{ stat_data: { hp: 9 } }, { stat_data: { hp: 12 } }])
  })

  it('只接受同源父窗口、正确版本号的变量消息', () => {
    const { sandbox, emitMessage } = makeSandbox()
    const getAllVariables = sandbox.getAllVariables as () => unknown
    const before = JSON.stringify(getAllVariables())

    emitMessage({ source: {}, data: { v: 1, type: 'flyai-card-vars', variables: { hp: 1 } } })
    emitMessage({
      source: sandbox.host,
      data: { v: 2, type: 'flyai-card-vars', variables: { hp: 1 } },
    })
    expect(JSON.stringify(getAllVariables())).toBe(before)

    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 9 } } },
    })
    expect(getAllVariables()).toEqual({ stat_data: { hp: 9 } })
  })

  it('事件总线支持 on/once/off，并广播变量更新', () => {
    const { sandbox, emitMessage } = makeSandbox()
    const calls: unknown[] = []
    const eventOn = sandbox.eventOn as (event: string, fn: (value: unknown) => void) => void
    const eventOnce = sandbox.eventOnce as (event: string, fn: (value: unknown) => void) => void
    const eventOff = sandbox.eventOff as (event: string, fn: (value: unknown) => void) => void
    const handler = (value: unknown) => calls.push(value)
    eventOn('mag_variable_update_ended', handler)
    eventOnce('custom', handler)

    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 9 } } },
    })
    expect(calls).toEqual([{ stat_data: { hp: 9 } }])

    const eventEmit = sandbox.eventEmit as (event: string) => void
    eventEmit('custom')
    eventEmit('custom')
    expect(calls).toHaveLength(2)

    eventOff('mag_variable_update_ended', handler)
    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 10 } } },
    })
    expect(calls).toHaveLength(2)
  })

  it('alert/toastr 走宿主提示，confirm 只读拒绝、可写放行，prompt 返回空', () => {
    const { sandbox, posted } = makeSandbox()
    const alertFn = sandbox.alert as (message: string) => void
    alertFn('提示')
    const toastr = sandbox.toastr as { success: (message: string) => void }
    toastr.success('完成')
    expect(toasts(posted).map((item) => item.text)).toEqual(['提示', '完成'])
    // 只读卡：confirm 一律拒绝；可写卡：放行到宿主逐次确认
    expect((sandbox.confirm as () => boolean)()).toBe(false)
    const writable = makeSandbox('x', null, null, {
      actions: false,
      write: true,
      net: false,
    })
    expect((writable.sandbox.confirm as () => boolean)()).toBe(true)
    expect((sandbox.prompt as () => string | null)()).toBeNull()
  })

  it('errorCatched 吞掉同步与异步异常', async () => {
    const { sandbox } = makeSandbox()
    const errorCatched = sandbox.errorCatched as (
      fn: (...args: unknown[]) => unknown
    ) => (...args: unknown[]) => unknown
    const sync = errorCatched(() => {
      throw new Error('x')
    })
    expect(sync()).toBeUndefined()

    const asyncFn = errorCatched(() => Promise.reject(new Error('y')))
    await expect(asyncFn()).resolves.toBeUndefined()
  })

  it('waitGlobalInitialized 在目标已存在时立即完成', async () => {
    const { sandbox } = makeSandbox()
    sandbox.someGlobal = { ready: true }
    const wait = sandbox.waitGlobalInitialized as (name: string) => Promise<void>
    await expect(wait('someGlobal')).resolves.toBeUndefined()
    await expect(wait('Mvu')).resolves.toBeUndefined()
  })

  it('SillyTavern.getContext 只暴露同一份变量快照', () => {
    const { sandbox } = makeSandbox('', { stat_data: { mood: 'calm' } })
    const SillyTavern = sandbox.SillyTavern as {
      getContext: () => { variables: unknown }
    }
    expect(SillyTavern.getContext().variables).toEqual({ stat_data: { mood: 'calm' } })
  })

  it('L3 别名：getLastMessage、按类型 getVariables、批量 getChatMessages', () => {
    const { sandbox } = makeSandbox('最后一条')
    expect((sandbox.getLastMessage as () => string)()).toBe('最后一条')
    const getVariables = sandbox.getVariables as (options?: {
      type?: string
    }) => unknown
    expect(getVariables({ type: 'message' })).toEqual({ stat_data: { hp: 7 } })
    expect(getVariables({ type: 'character' })).toEqual({})
    expect(getVariables({ type: 'script' })).toEqual({})

    const getChatMessages = sandbox.getChatMessages as (
      id?: unknown
    ) => Array<{
      message_id: number
      message: string
      swipes: string[]
      extra: { variables: unknown }
    }>
    const list = getChatMessages([3, '4'])
    expect(list.map((item) => item.message_id)).toEqual([3, 4])
    expect(list[0].message).toBe('最后一条')
    expect(list[0].swipes).toEqual(['最后一条'])
    expect(list[0].extra.variables).toEqual({ stat_data: { hp: 7 } })

    const helper = sandbox.TavernHelper as Record<string, unknown>
    expect((helper.getLastMessage as () => string)()).toBe('最后一条')
    expect(typeof helper.getWorldbookNames).toBe('function')
    expect(typeof helper.getCharName).toBe('function')
  })

  it('L3：角色名/头像与常用宏替换，写入仍被拒绝', () => {
    const { sandbox, posted } = makeSandbox('你好', { stat_data: { hp: 7 } }, {
      name: '角色甲',
      avatar: 'https://cdn.example.com/a.png',
    })
    expect((sandbox.getCharName as () => string)()).toBe('角色甲')
    expect((sandbox.getCurrentCharName as () => string)()).toBe('角色甲')
    expect((sandbox.getCharAvatar as () => string)()).toBe('https://cdn.example.com/a.png')

    const sub = sandbox.substituteParams as (value: string) => string
    expect(sub('{{char}} / {{user}} / {{getvar::hp}}')).toBe('角色甲 /  / 7')
    expect(sub('{{lastMessage}}')).toBe('你好')
    expect(sub('{{charAvatar}}')).toBe('https://cdn.example.com/a.png')
    expect(sub('{{getglobalvar::x}}')).toBe('')
    expect(sub('{{unknown}}')).toBe('{{unknown}}')
    expect(sub(42 as unknown as string)).toBe(42)
    // SillyTavern 的历史拼写：substitudeMacros
    const stSub = sandbox.substitudeMacros as (value: string) => string
    expect(typeof stSub).toBe('function')
    expect(stSub('{{char}}')).toBe('角色甲')

    const context = (sandbox.SillyTavern as {
      getContext: () => {
        characters: Array<{ name: string; avatar: string }>
        name1: string
        name2: string
      }
    }).getContext()
    expect(context.characters[0]).toMatchObject({ name: '角色甲', avatar: 'https://cdn.example.com/a.png' })
    // 楼层检测兼容：name2 必须是角色名，name1 是宿主用户名
    expect(context.name2).toBe('角色甲')
    expect(context.name1).toBe('')

    const setVariables = sandbox.setVariables as () => boolean
    const insertVariables = sandbox.insertVariables as () => boolean
    expect(setVariables()).toBe(false)
    expect(insertVariables()).toBe(false)
    expect(toasts(posted).filter((item) => item.code === 'read-only')).toHaveLength(2)
  })

  it('L3 世界书：读取同步返回空表，写入提示只读但安全完成', async () => {
    const { sandbox, posted } = makeSandbox()
    // SillyTavern 语义：名称列表同步返回数组，卡片会直接调用 .includes
    const getWorldbookNames = sandbox.getWorldbookNames as () => string[]
    const getCharWorldbookNames = sandbox.getCharWorldbookNames as () => {
      primary: string | null
      additional: string[]
    }
    const getGlobalWorldbookNames = sandbox.getGlobalWorldbookNames as () => string[]
    const getWorldbook = sandbox.getWorldbook as (name: string) => unknown[]
    const updateWorldbookWith = sandbox.updateWorldbookWith as () => Promise<unknown>
    const replaceWorldbook = sandbox.replaceWorldbook as () => Promise<unknown>
    expect(Array.isArray(getWorldbookNames())).toBe(true)
    expect(getWorldbookNames().includes('x')).toBe(false)
    expect(getCharWorldbookNames()).toEqual({ primary: null, additional: [] })
    expect(getGlobalWorldbookNames()).toEqual([])
    expect(getWorldbook('x')).toEqual([])
    await expect(updateWorldbookWith()).resolves.toBeUndefined()
    await expect(replaceWorldbook()).resolves.toBeUndefined()
    expect(toasts(posted).filter((item) => item.code === 'read-only')).toHaveLength(2)
  })

  it('L3 错误上报：console.error 与异常事件进入角标，去重且有上限', () => {
    const { sandbox, posted, emit } = makeSandbox()
    const errors = () => posted.filter((item) => item.type === 'flyai-card-error')

    const consoleError = (sandbox.console as { error: (value: unknown) => void }).error
    consoleError('boom')
    consoleError('boom')
    expect(errors()).toHaveLength(1)
    expect(errors()[0].text).toBe('boom')

    emit('error', { error: new Error('运行时错误') })
    emit('unhandledrejection', { reason: new Error('异步错误') })
    expect(errors().map((item) => item.text)).toEqual(['boom', '运行时错误', '异步错误'])

    for (let i = 0; i < 40; i += 1) {
      consoleError(`err-${i}`)
    }
    expect(errors()).toHaveLength(20)
  })

  it('聊天镜像：getContext 暴露 0 楼聊天，createChatMessages/generate 交给宿主发送', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox(
      '开场白',
      null,
      { name: '角色甲', avatar: 'a.png' },
      { actions: true, write: false, net: false },
      {
        chatId: 'chat-1',
        userName: '玩家',
        characterName: '角色甲',
        characterAvatar: 'a.png',
        messages: [{ role: 'assistant', name: '角色甲', text: '开场白' }],
      }
    )
    type Context = {
      chat: Array<Record<string, unknown>>
      chatId: string
      name1: string
      name2: string
      saveChat: () => Promise<boolean>
      generate: () => Promise<boolean>
      getCurrentMessageId: () => number
    }
    const context = (sandbox.SillyTavern as { getContext: () => Context }).getContext()
    expect(context.chatId).toBe('chat-1')
    expect(context.name1).toBe('玩家')
    expect(context.name2).toBe('角色甲')
    expect(context.getCurrentMessageId()).toBe(0)
    expect(context.chat).toHaveLength(1)
    expect(context.chat[0]).toMatchObject({ is_user: false, mes: '开场白' })
    await expect(context.saveChat()).resolves.toBe(true)

    const create = sandbox.createChatMessages as (messages: unknown[]) => Promise<
      Array<Record<string, unknown>>
    >
    const created = await create([
      {
        role: 'user',
        message: '开始约会',
        data: {
          schema: '没有用别管这个',
          stat_data: { 世界: { 日期: '' }, 玩家: {}, 角色: {}, 剧情: {} },
          initialized_lorebooks: { book: [] },
        },
      },
    ])
    expect(created).toHaveLength(1)
    expect(context.chat).toHaveLength(2)
    expect(context.chat[1]).toMatchObject({ is_user: true, mes: '开始约会' })
    // 消息变量按 swipe 下标存放，保留完整信封
    expect(context.chat[1].variables).toEqual([
      {
        schema: '没有用别管这个',
        stat_data: { 世界: { 日期: '' }, 玩家: {}, 角色: {}, 剧情: {} },
        initialized_lorebooks: { book: [] },
      },
    ])

    // generate() 把 createChatMessages 暂存的用户文本交给宿主确认发送，
    // 并等待回复楼层落地后才 resolve
    const generated = context.generate()
    expect(posted.filter((item) => item.type === 'flyai-card-action').map((item) => item.intent)).toEqual([
      { kind: 'action', action: 'send', text: '开始约会' },
    ])
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-chat',
        chat: {
          chatId: 'chat-1',
          userName: '玩家',
          messages: [
            { role: 'assistant', name: '角色甲', text: '开场白' },
            { role: 'user', name: '玩家', text: '开始约会' },
            { role: 'assistant', name: '角色甲', text: '回复' },
          ],
        },
      },
    })
    await expect(generated).resolves.toBe(true)

    const detail = sandbox.getChatHistoryDetail as (files?: unknown[]) => Promise<
      Record<string, unknown[]>
    >
    const history = await detail()
    expect(history['chat-1.jsonl']).toBe(context.chat)
  })

  it('write：createChatMessages 追加楼层落库，并把后端 id 映射回镜像', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox(
      '开场白',
      null,
      { name: '角色甲' },
      { actions: true, write: true, net: false },
      {
        chatId: 'chat-1',
        userName: '玩家',
        characterName: '角色甲',
        messages: [{ id: 'm0', role: 'assistant', text: '开场白' }],
      }
    )
    type Context = {
      chat: Array<Record<string, unknown>>
      generate: (type?: string, options?: Record<string, unknown>) => Promise<boolean>
    }
    const context = (sandbox.SillyTavern as { getContext: () => Context }).getContext()
    const create = sandbox.createChatMessages as (messages: unknown[]) => Promise<
      Array<Record<string, unknown>>
    >
    const pending = create([
      {
        role: 'user',
        name: '玩家',
        message: '开场正文',
        extra: { dal_opening: { version: 2, id: 'op-1', mode: 'body' } },
        data: { stat_data: { hp: 1 }, schema: 'x' },
      },
    ])
    const patch = posted.find((item) => item.type === 'flyai-card-messages-patch')
    expect(patch?.changes).toEqual([])
    expect(patch?.creates).toEqual([
      {
        role: 'user',
        name: '玩家',
        message: '开场正文',
        extra: { dal_opening: { version: 2, id: 'op-1', mode: 'body' } },
        data: { stat_data: { hp: 1 }, schema: 'x' },
        is_hidden: false,
      },
    ])
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-messages-result',
        id: patch?.id,
        ok: true,
        result: {
          updated: [],
          created: [{ id: 'srv-1', message_id: 1, role: 'user' }],
        },
      },
    })
    const created = await pending
    expect(created[0].id).toBe('srv-1')
    expect(context.chat).toHaveLength(2)
    expect(context.chat[1]).toMatchObject({
      id: 'srv-1',
      is_user: true,
      mes: '开场正文',
      extra: { dal_opening: { version: 2, id: 'op-1', mode: 'body' } },
    })
    // create 已落库后调 generate()：不再重复发送 user 楼层，只请求生成回复
    const generated = context.generate('normal')
    const generateMessage = posted.find((item) => item.type === 'flyai-card-generate')
    expect(generateMessage?.quiet_prompt).toBe('')
    expect(posted.some((item) => item.type === 'flyai-card-action')).toBe(false)
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-generate-result',
        id: generateMessage?.id,
        ok: true,
      },
    })
    await expect(generated).resolves.toBe(true)
  })

  it('聊天镜像：宿主更新保留 chat[0] 引用与 extra 标记', () => {
    const { sandbox, emitMessage } = makeSandbox(
      '开场白',
      null,
      { name: '角色甲' },
      null,
      {
        chatId: 'chat-1',
        userName: '玩家',
        characterName: '角色甲',
        messages: [{ role: 'assistant', text: '开场白' }],
      }
    )
    type Context = {
      chat: Array<Record<string, unknown>>
      chatId: string
      name1: string
    }
    const context = (sandbox.SillyTavern as { getContext: () => Context }).getContext()
    const opening = context.chat[0]
    opening.extra = { dal_opening: true }
    const envelope = {
      schema: '没有用别管这个',
      stat_data: { hp: 1 },
      initialized_lorebooks: {},
    }
    opening.variables = [envelope]

    // 模拟宿主窗口发来的聊天快照（source 必须是宿主窗口）
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-chat',
        chat: {
          chatId: 'chat-2',
          userName: '新玩家',
          messages: [
            {
              role: 'assistant',
              name: '角色甲',
              text: '更新后的开场白',
              statData: { hp: 9 },
            },
            { role: 'user', name: '新玩家', text: '你好' },
          ],
        },
      },
    })
    expect(context.chat[0]).toBe(opening)
    expect(context.chat[0].mes).toBe('更新后的开场白')
    expect(context.chat[0].extra).toEqual({ dal_opening: true })
    // 宿主快照只有 stat_data，不能覆盖卡写入的完整变量信封
    expect(context.chat[0].variables).toEqual([envelope])
    expect(context.chat).toHaveLength(2)
    const updated = (sandbox.SillyTavern as { getContext: () => Context }).getContext()
    expect(updated.chatId).toBe('chat-2')
    expect(updated.name1).toBe('新玩家')
  })

  it('世界书 HTTP：/api/worldinfo/get|edit 走本地副本，写入交给宿主确认', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox(
      'hi',
      null,
      null,
      { actions: false, write: true, net: false }
    )
    const fetchFn = sandbox.fetch as (
      url: string,
      init?: { method?: string; body?: string }
    ) => Promise<{ json: () => Promise<Record<string, unknown>> }>

    const empty = await fetchFn('/api/worldinfo/get', {
      method: 'POST',
      body: JSON.stringify({ name: '约会世界' }),
    })
    expect(await empty.json()).toEqual({ entries: {} })

    const pendingEdit = fetchFn('/api/worldinfo/edit', {
      method: 'POST',
      body: JSON.stringify({
        name: '约会世界',
        data: {
          entries: {
            0: { key: ['精灵'], content: '十万个为什么', comment: '设定' },
          },
        },
      }),
    })
    await Promise.resolve()
    const writeIntent = posted.find((item) => item.type === 'flyai-card-action')
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-action-result',
        id: writeIntent?.id,
        ok: true,
        result: { entries: [] },
      },
    })
    const edited = await pendingEdit
    expect(await edited.json()).toEqual({ ok: true })

    const world = posted.filter((item) => {
      const intent = item.intent as Record<string, unknown> | undefined
      return item.type === 'flyai-card-action' && intent?.kind === 'world'
    })
    expect(world).toHaveLength(1)
    expect(world[0].intent).toMatchObject({
      kind: 'world',
      entries: [
        {
          title: '设定',
          content: '十万个为什么',
          keys: ['精灵'],
          pinned: false,
        },
      ],
    })

    // 读回本地副本
    const read = await fetchFn('/api/worldinfo/get', {
      method: 'POST',
      body: JSON.stringify({ name: '约会世界' }),
    })
    expect(await read.json()).toMatchObject({
      entries: { 0: { content: '十万个为什么' } },
    })
  })

  it('世界书 HTTP：无写权限时 edit 被拒绝并提示只读', async () => {
    const { sandbox, posted } = makeSandbox()
    const fetchFn = sandbox.fetch as (url: string, init?: { body?: string }) => Promise<unknown>
    await expect(
      fetchFn('/api/worldinfo/edit', {
        body: JSON.stringify({ name: 'x', data: { entries: { 0: { content: 'c' } } } }),
      })
    ).rejects.toThrow('flyai-card-read-only')
    expect(toasts(posted).map((item) => item.code)).toContain('read-only')
  })

  it('角色卡内嵌世界书：按卡的 slots 映射 uid，供 /api/worldinfo/get 读取', async () => {
    const { sandbox, emitMessage } = makeSandbox(
      'hi',
      null,
      null,
      { actions: false, write: true, net: false }
    )
    ;(sandbox as Record<string, unknown>).DALOpeningWorldbook = {
      slots: [
        { uid: 265, title: '[本局] 玩家资料', constant: true },
        { uid: 266, title: '[本局] 世界设定', constant: true },
        { uid: 267, title: '[本局] 原创NPC 1', constant: false },
      ],
    }
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-write-context',
        context: {
          characterWorldName: '约会大作战P-v1',
          worldbook: null,
          character: {
            table: [
              { title: '[本局] 玩家资料', content: '待填写', pinned: false, keys: [] },
              { title: '[本局] 世界设定', content: '待填写', pinned: false, keys: [] },
              { title: '[本局] 原创NPC 1', content: '待填写', pinned: false, keys: [] },
              { title: '普通条目', content: '正文', pinned: true, keys: ['甲'] },
            ],
          },
        },
      },
    })
    const names = sandbox.getCharWorldbookNames as (type?: string) => {
      primary: string | null
      additional: string[]
    }
    expect(names('current')).toEqual({ primary: '约会大作战P-v1', additional: [] })

    const fetchFn = sandbox.fetch as (
      url: string,
      init?: { method?: string; body?: string }
    ) => Promise<{ json: () => Promise<{ entries: Record<string, Record<string, unknown>> }> }>
    const res = await fetchFn('/api/worldinfo/get', {
      method: 'POST',
      body: JSON.stringify({ name: '约会大作战P-v1' }),
    })
    const book = await res.json()
    expect(book.entries['265']).toMatchObject({
      uid: 265,
      comment: '[本局] 玩家资料',
      constant: true,
      content: '待填写',
      key: [],
      disable: false,
    })
    expect(book.entries['267']).toMatchObject({ uid: 267, constant: false })
    // 没有 manifest 声明的条目用稳定序号，避开 slots 的 uid
    const plain = Object.values(book.entries).find(
      (entry) => entry.comment === '普通条目'
    )
    expect(plain).toMatchObject({ content: '正文', constant: true, key: ['甲'] })
    expect([265, 266, 267]).not.toContain(plain?.uid)
  })

  it('navigator.locks 兼容：不抛 SecurityError，同名单串行执行', async () => {
    const { sandbox } = makeSandbox()
    type LockManagerLike = {
      request: (
        name: string,
        options: unknown,
        callback: (lock: unknown) => unknown
      ) => Promise<unknown>
      query: () => Promise<{ held: unknown[]; pending: unknown[] }>
    }
    const locks = (sandbox.navigator as { locks: LockManagerLike }).locks
    expect(locks).toBeTruthy()
    const order: string[] = []
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const first = locks.request('book', { ifAvailable: true }, async () => {
      await gate
      order.push('first')
      return 1
    })
    const second = locks.request('book', { ifAvailable: true }, () => {
      order.push('second')
      return 2
    })
    // 第二把锁必须等第一把完成，而不是被 ifAvailable 直接跳过
    await Promise.resolve()
    expect(order).toEqual([])
    release()
    await expect(first).resolves.toBe(1)
    await expect(second).resolves.toBe(2)
    expect(order).toEqual(['first', 'second'])
    await expect(locks.query()).resolves.toEqual({ held: [], pending: [] })
  })
})

describe('L4 授权能力', () => {
  const actionsOnly: CardAppCapabilities = { actions: true, write: false, net: false }
  const writeOnly: CardAppCapabilities = { actions: false, write: true, net: false }
  const netOnly: CardAppCapabilities = { actions: false, write: false, net: true }

  it('默认不授予任何能力：发送被拒绝、网络请求被拒', async () => {
    const { sandbox, posted } = makeSandbox()
    expect((sandbox.FlyAI as { capabilities: unknown }).capabilities).toEqual({
      actions: false,
      write: false,
      net: false,
    })
    // 世界书本地兼容层需要 fetch 存在；外网请求仍被拒绝，XHR 不安装
    expect(typeof sandbox.fetch).toBe('function')
    const fetchFn = sandbox.fetch as (url: string) => Promise<unknown>
    await expect(fetchFn('https://example.com/api')).rejects.toThrow()
    expect(sandbox.XMLHttpRequest).toBeUndefined()

    const triggerSlash = sandbox.triggerSlash as (command: string) => string
    expect(triggerSlash('/send 你好')).toBe('')
    expect(posted.some((item) => item.type === 'flyai-card-action')).toBe(false)
    expect(toasts(posted).map((item) => item.code)).toEqual(['read-only'])

    const updateCharacter = (sandbox.FlyAI as {
      updateCharacter: (fields: unknown) => Promise<unknown>
    }).updateCharacter
    await expect(updateCharacter({ name: 'x' })).rejects.toThrow('flyai-card-read-only')
  })

  it('actions：发送/继续/重生成发意图并带自增 id', () => {
    const { sandbox, posted } = makeSandbox('x', null, null, actionsOnly)
    const slash = sandbox.triggerSlash as (command: string) => string
    slash('/send 你好')
    slash('/continue')
    slash('/regenerate')

    const actions = posted.filter((item) => item.type === 'flyai-card-action')
    expect(actions.map((item) => item.intent)).toEqual([
      { kind: 'action', action: 'send', text: '你好' },
      { kind: 'action', action: 'continue' },
      { kind: 'action', action: 'regenerate' },
    ])
    expect(actions.map((item) => item.id)).toEqual(['a1', 'a2', 'a3'])
    // 已授权时不再发只读提示
    expect(toasts(posted)).toHaveLength(0)
  })

  it('actions：斜杠管线按 ST 语义拆链，`|/trigger` 不混入发送文本', () => {
    // 状态面板卡用 `triggerSlash(`/send ${text}|/trigger`)` 串发送与快捷回复；
    // 不拆链会让 `|/trigger` 成为消息正文的一部分被发给模型。
    const { sandbox, posted } = makeSandbox('x', null, null, actionsOnly)
    const slash = sandbox.triggerSlash as (command: string) => string
    slash('/send 你好|/trigger')
    // `\|` 是转义的字面竖线，应保留
    slash('/send a\\|b|/trigger')
    // `/trigger` 单独调用无副作用（快捷回复未移植），也不应报只读
    slash('/trigger')

    const actions = posted.filter((item) => item.type === 'flyai-card-action')
    expect(actions.map((item) => item.intent)).toEqual([
      { kind: 'action', action: 'send', text: '你好' },
      { kind: 'action', action: 'send', text: 'a|b' },
    ])
    expect(toasts(posted)).toHaveLength(0)
  })

  it('write：设置变量乐观更新并发出 patch 意图，宿主快照可校正', () => {
    const { sandbox, posted, emitMessage } = makeSandbox(
      'x',
      { stat_data: { hp: 7 } },
      null,
      writeOnly
    )
    const setChatVariable = sandbox.setChatVariable as (key: string, value: unknown) => boolean
    expect(setChatVariable('hp', 9)).toBe(true)
    const message = posted.find((item) => item.type === 'flyai-card-action')
    expect(message?.intent).toEqual({
      kind: 'vars',
      mode: 'patch',
      ops: [{ op: 'add', path: '/hp', value: 9 }],
    })
    expect((sandbox.getChatVariable as (key: string) => unknown)('hp')).toBe(9)

    const deleteVariable = sandbox.deleteVariable as (
      key: string
    ) => { variables: unknown; delete_occurred: boolean }
    expect(deleteVariable('hp')).toEqual({ variables: {}, delete_occurred: true })
    const remove = posted.filter((item) => item.type === 'flyai-card-action').at(-1)
    expect(remove?.intent).toEqual({ kind: 'vars', mode: 'patch', ops: [{ op: 'remove', path: '/hp' }] })

    // JSON Pointer 转义
    setChatVariable('a/b~c', 1)
    const escaped = posted.filter((item) => item.type === 'flyai-card-action').at(-1)
    expect(escaped?.intent).toEqual({
      kind: 'vars',
      mode: 'patch',
      ops: [{ op: 'add', path: '/a~1b~0c', value: 1 }],
    })

    emitMessage({
      source: sandbox.host,
      data: { v: 1, type: 'flyai-card-vars', variables: { stat_data: { hp: 3 } } },
    })
    expect((sandbox.getChatVariable as (key: string) => unknown)('hp')).toBe(3)
  })

  it('write：整体替换变量发 replace 意图', () => {
    const { sandbox, posted } = makeSandbox('x', null, null, writeOnly)
    const setVariables = sandbox.setVariables as (next: unknown) => boolean
    expect(setVariables({ hp: 1, mp: 2 })).toBe(true)
    const message = posted.find((item) => item.type === 'flyai-card-action')
    expect(message?.intent).toEqual({
      kind: 'vars',
      mode: 'replace',
      stat_data: { hp: 1, mp: 2 },
    })
    expect((sandbox.getAllVariables as () => unknown)()).toEqual({ stat_data: { hp: 1, mp: 2 } })
  })

  it('write：世界书写回只针对宿主下发的副本，结果回填', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, writeOnly)
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-write-context',
        context: {
          worldbook: { name: '世界书', entries: [{ title: 'A' }] },
          character: { name: '甲', intro: '介绍', id: 999 },
        },
      },
    })
    expect((sandbox.getWorldbookNames as () => string[])().includes('世界书')).toBe(true)
    expect((sandbox.getCharWorldbookNames as () => { primary: string | null })()).toEqual({
      primary: '世界书',
      additional: [],
    })
    const worldbook = (sandbox.getWorldbook as (name: string) => unknown[])('世界书')
    expect(worldbook.map((entry) => (entry as { title?: string }).title)).toEqual(['A'])
    expect(
      (sandbox.FlyAI as { getCharacterFields: () => unknown }).getCharacterFields()
    ).toEqual({ name: '甲', intro: '介绍', id: 999 })

    const pending = (sandbox.replaceWorldbook as (entries: unknown[]) => Promise<unknown>)([
      { title: 'B' },
    ])
    const message = posted.find((item) => item.type === 'flyai-card-action')
    const intent = message?.intent as { kind: string; entries: Array<{ title?: string }> }
    expect(intent.kind).toBe('world')
    expect(intent.entries.map((entry) => entry.title)).toEqual(['B'])
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-action-result',
        id: message?.id,
        ok: true,
        result: { entries: [{ title: 'B' }] },
      },
    })
    await expect(pending).resolves.toEqual([{ title: 'B' }])
  })

  it('write：无视卡传入的书名，读取角色卡自带书 + 挂载书的合并视图', async () => {
    const { sandbox, emitMessage } = makeSandbox('x', null, null, writeOnly)
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-write-context',
        context: {
          worldbook: { name: '挂载书', entries: [{ title: 'B' }, { title: 'A' }] },
          characterWorldName: '卡内书',
          character: { name: '甲', table: [{ title: 'A' }, { title: 'C' }] },
        },
      },
    })
    const names = (sandbox.getWorldbookNames as () => string[])()
    expect(names).toEqual(['卡内书', '挂载书'])
    expect((sandbox.getCharWorldbookNames as () => { primary: string | null })()).toEqual({
      primary: '卡内书',
      additional: [],
    })
    // 卡期望的书名与两份书都不匹配时仍然返回全部条目；A 在两份书中重复只保留一份
    const getWorldbook = sandbox.getWorldbook as (name: string) => unknown[]
    const titles = (name: string) =>
      getWorldbook(name).map((entry) => (entry as { title?: string }).title)
    expect(titles('卡片期望的名字')).toEqual(['A', 'C', 'B'])
    expect(titles('卡内书')).toEqual(['A', 'C', 'B'])
  })

  it('write：没有挂载世界书时，写回落到会话角色副本的 table', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, writeOnly)
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-write-context',
        context: {
          worldbook: null,
          characterWorldName: '卡内书',
          character: { name: '甲', table: [{ title: 'A' }] },
        },
      },
    })
    const updateWorldbookWith = sandbox.updateWorldbookWith as (
      name: string,
      updater: (entries: unknown[]) => unknown[]
    ) => Promise<unknown>
    const pending = updateWorldbookWith('卡内书', (entries) => [
      ...entries,
      { title: '新增' },
    ])
    const message = posted.find((item) => item.type === 'flyai-card-action')
    const intent = message?.intent as {
      kind: string
      fields: { table: Array<{ title?: string }> }
    }
    expect(intent.kind).toBe('character')
    expect(intent.fields.table.map((entry) => entry.title)).toEqual(['A', '新增'])
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-action-result',
        id: message?.id,
        ok: true,
      },
    })
    const titlesOf = (entries: unknown[]) =>
      entries.map((entry) => (entry as { title?: string }).title)
    const resolved = (await pending) as unknown[]
    expect(titlesOf(resolved)).toEqual(['A', '新增'])
    expect(
      titlesOf((sandbox.getWorldbook as (name: string) => unknown[])('卡内书'))
    ).toEqual(['A', '新增'])
  })

  it('write：角色字段写回发 character 意图，拒绝时 reject', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, writeOnly)
    const updateCharacter = (sandbox.FlyAI as {
      updateCharacter: (fields: unknown) => Promise<unknown>
    }).updateCharacter
    const pending = updateCharacter({ name: '乙' })
    const message = posted.find((item) => item.type === 'flyai-card-action')
    expect(message?.intent).toEqual({ kind: 'character', fields: { name: '乙' } })
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-action-result',
        id: message?.id,
        ok: false,
        error: 'unavailable',
      },
    })
    await expect(pending).rejects.toThrow('unavailable')
  })

  it('write：chatMetadata 走宿主桥，setChatMessages 按消息 id 落库', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, writeOnly, {
      chatId: 'c1',
      userName: '你',
      characterName: '甲',
      messages: [
        { id: 'm1', role: 'assistant', text: '旧正文' },
        { id: 'm2', role: 'user', text: '提问' },
      ],
    })
    const updateChatMetadata = sandbox.updateChatMetadata as (
      patch: Record<string, unknown>,
      reset?: boolean
    ) => void
    updateChatMetadata({ map: { a: 1 } })
    const context = (
      sandbox.SillyTavern as { getContext: () => { chatMetadata: unknown } }
    ).getContext()
    expect(context.chatMetadata).toEqual({ map: { a: 1 } })

    const saveMetadata = sandbox.saveMetadata as () => Promise<unknown>
    const saving = saveMetadata()
    const metadataMessage = posted.find((item) => item.type === 'flyai-card-metadata-set')
    expect(metadataMessage?.metadata).toEqual({ map: { a: 1 } })
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-metadata-result',
        id: metadataMessage?.id,
        ok: true,
      },
    })
    await expect(saving).resolves.toBeUndefined()

    const setChatMessages = sandbox.setChatMessages as (
      changes: unknown[]
    ) => Promise<unknown>
    const pending = setChatMessages([{ message_id: 0, message: '新正文' }])
    const patchMessage = posted.find((item) => item.type === 'flyai-card-messages-patch')
    expect(patchMessage?.changes).toEqual([{ id: 'm1', message: '新正文' }])
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-messages-result',
        id: patchMessage?.id,
        ok: true,
        result: { updated: ['m1'] },
      },
    })
    await pending
  })

  it('write：localStorage 写入落到 chatMetadata 保留键', () => {
    const { sandbox, posted } = makeSandbox('x', null, null, writeOnly)
    const storage = sandbox.localStorage as {
      setItem: (key: string, value: string) => void
      getItem: (key: string) => string | null
      removeItem: (key: string) => void
    }
    storage.setItem('k', 'v')
    expect(storage.getItem('k')).toBe('v')
    // removeItem 会立即 flush，避免测试等待 400ms 防抖
    storage.removeItem('missing')
    const message = posted.find((item) => item.type === 'flyai-card-metadata-set')
    expect(
      (message?.metadata as { __flyai_storage_local?: unknown }).__flyai_storage_local
    ).toEqual({ k: 'v' })
  })

  it('actions：context.generate 的 quiet_prompt 走主聊天生成桥', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, {
      actions: true,
      write: true,
      net: false,
    })
    const context = (
      sandbox.SillyTavern as {
        getContext: () => {
          generate: (type: string, options: { quiet_prompt: string }) => Promise<unknown>
        }
      }
    ).getContext()
    const pending = context.generate('normal', { quiet_prompt: '系统旁白' })
    const message = posted.find((item) => item.type === 'flyai-card-generate')
    expect(message?.quiet_prompt).toBe('系统旁白')
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-generate-result',
        id: message?.id,
        ok: true,
      },
    })
    await expect(pending).resolves.toBe(true)
  })

  it('net：fetch 经宿主代理，请求头白名单与响应还原', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, netOnly)
    const fetchFn = sandbox.fetch as (
      url: string,
      init?: Record<string, unknown>
    ) => Promise<Response>
    const pending = fetchFn('https://example.com/a?b=1', {
      headers: { Accept: 'text/plain', Authorization: 'secret', Cookie: 'x=1' },
    })
    const message = posted.find((item) => item.type === 'flyai-card-fetch')
    expect(message?.request).toEqual({
      method: 'GET',
      url: 'https://example.com/a?b=1',
      headers: { accept: 'text/plain' },
      bodyBase64: '',
    })
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-fetch-result',
        id: message?.id,
        ok: true,
        status: 200,
        headers: { 'content-type': 'text/plain' },
        bodyBase64: btoa('hello'),
      },
    })
    const response = await pending
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('text/plain')
    expect(await response.text()).toBe('hello')
  })

  it('net：POST body 转 base64，非白名单方法与协议直接拒绝', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, netOnly)
    const fetchFn = sandbox.fetch as (
      url: string,
      init?: Record<string, unknown>
    ) => Promise<Response>
    const pending = fetchFn('https://example.com/submit', {
      method: 'POST',
      body: 'hi',
      headers: { 'Content-Type': 'text/plain' },
    })
    const message = posted.find((item) => item.type === 'flyai-card-fetch')
    expect(message?.request?.method).toBe('POST')
    expect(atob(message?.request?.bodyBase64 as string)).toBe('hi')
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-fetch-result',
        id: message?.id,
        ok: true,
        status: 204,
        headers: {},
        bodyBase64: '',
      },
    })
    await expect(pending).resolves.toBeInstanceOf(Response)

    await expect(fetchFn('https://example.com', { method: 'PUT' })).rejects.toThrow(
      'Failed to fetch'
    )
    await expect(fetchFn('file:///etc/passwd')).rejects.toThrow('Failed to fetch')
    expect(posted.filter((item) => item.type === 'flyai-card-fetch')).toHaveLength(1)
  })

  it('net：策略拒绝带 flyaiCode，abort 发取消消息并抛 AbortError', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, netOnly)
    const fetchFn = sandbox.fetch as (
      url: string,
      init?: Record<string, unknown>
    ) => Promise<Response>

    const denied = fetchFn('http://127.0.0.1/admin')
    const deniedMessage = posted.find((item) => item.type === 'flyai-card-fetch')
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-fetch-result',
        id: deniedMessage?.id,
        ok: false,
        error: 'net-denied',
      },
    })
    const error = await denied.catch((err: unknown) => err)
    expect((error as { flyaiCode?: string }).flyaiCode).toBe('net-denied')

    const controller = new AbortController()
    const pending = fetchFn('https://example.com/slow', { signal: controller.signal })
    controller.abort()
    await expect(pending).rejects.toThrow('aborted')
    expect(posted.some((item) => item.type === 'flyai-card-fetch-abort')).toBe(true)
  })

  it('net：XMLHttpRequest 复用同一代理通道', async () => {
    const { sandbox, posted, emitMessage } = makeSandbox('x', null, null, netOnly)
    const XHR = sandbox.XMLHttpRequest as new () => {
      open: (method: string, url: string) => void
      send: () => void
      status: number
      responseText: string
      onload: (() => void) | null
    }
    const xhr = new XHR()
    const loaded = new Promise<void>((resolve) => {
      xhr.onload = () => resolve()
    })
    xhr.open('GET', 'https://example.com/xhr')
    xhr.send()
    const message = posted.find((item) => item.type === 'flyai-card-fetch')
    expect(message?.request?.url).toBe('https://example.com/xhr')
    emitMessage({
      source: sandbox.host,
      data: {
        v: 1,
        type: 'flyai-card-fetch-result',
        id: message?.id,
        ok: true,
        status: 200,
        headers: {},
        bodyBase64: btoa('ok'),
      },
    })
    await loaded
    expect(xhr.status).toBe(200)
    expect(xhr.responseText).toBe('ok')
  })
})
