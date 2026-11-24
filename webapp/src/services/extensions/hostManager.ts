import { useChatStore } from '../chat/store.js'
import { deviceKind } from '../device.js'
import { listWorlds, getWorld } from '../flyai/lore.js'
import { proxyNet, writeSessionMvu } from '../flyai/st.js'
import { request } from '../flyai/client.js'
import { showToast } from '../../components/base/toastState.js'
import { useExtensionErrors, useExtensionWidgets } from './widgets.js'
import type { ExtensionHostState } from './types.js'
import { EXT_NS, EXT_VERSION, hasCapability, isExtMessage, messageTooLarge } from './hostProtocol.js'
import type { ExtMessage } from './hostProtocol.js'
import { buildExtensionDoc, type ExtensionSnapshot } from './hostDoc.js'
import * as extClient from './client.js'

type Mounted = {
  state: ExtensionHostState
  iframe: HTMLIFrameElement
  ready: boolean
  dead: boolean
  settings: Record<string, unknown>
  commands: Set<string>
  errors: string[]
}

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: number }

const slashCommands = new Map<string, { name: string; help: string; ext: string }>()

const b64 = (text: string): string => {
  const bytes = new TextEncoder().encode(text)
  let out = ''
  bytes.forEach((byte) => {
    out += String.fromCharCode(byte)
  })
  return btoa(out)
}

const unb64 = (text: string): string => {
  const binary = atob(text)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new TextDecoder().decode(bytes)
}

const messageText = (content: unknown): string => {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((part) => (part && typeof part === 'object' && 'text' in part ? String((part as { text?: unknown }).text ?? '') : ''))
      .join('')
  }
  return ''
}

const buildSnapshot = (sessionId: string): ExtensionSnapshot => {
  const state = useChatStore.getState()
  const session = state.sessionCache[sessionId]
  const messages = session?.messages ?? []
  const chat = messages.map((message, index) => {
    const raw = message as unknown as Record<string, unknown>
    const role = String(raw.role || 'assistant')
    const text = messageText(raw.content)
    const stat = (raw.statData as Record<string, unknown> | undefined) || undefined
    return {
      message_id: index,
      name: String(raw.speakerName || (role === 'user' ? session?.userName : session?.characterName) || ''),
      is_user: role === 'user',
      is_system: role === 'system',
      mes: text,
      swipe_id: 0,
      extra: (raw.extra as Record<string, unknown>) || {},
      data: stat ? { stat_data: stat } : {},
    }
  })
  return {
    chat_id: sessionId,
    characters: session?.characterName
      ? [{ name: session.characterName, avatar: session.characterAvatar || '' }]
      : [],
    chat,
    chat_metadata: { variables: (session?.st?.mvu?.stat_data as Record<string, unknown>) || {} },
    name2: session?.characterName || '',
    user_name: session?.userName || 'User',
    user_avatar: 'user-default.png',
    character_avatar: session?.characterAvatar || '',
    is_mobile: deviceKind !== 'desktop',
    max_prompt_tokens: 8192,
    variables: (session?.st?.mvu?.stat_data as Record<string, unknown>) || {},
    extension_names: [],
    context: {
      chatId: sessionId,
      name2: session?.characterName || '',
      characterId: session?.characterId || '',
      onlineStatus: 'no_connection',
    },
  }
}

/**
 * L1 扩展宿主管理器：每个启用的扩展一个隐藏 iframe。
 * 只转发聊天事件与白名单 RPC；扩展代码永远拿不到登录态。
 */
class ExtensionHostManager {
  private sessionId = ''
  private container: HTMLDivElement | null = null
  private mounted = new Map<string, Mounted>()
  private pending = new Map<string, Pending>()
  private seq = 0
  private unsub: (() => void) | null = null
  private lastActive = ''
  private lastCounts = new Map<string, number>()
  private lastStreaming = false

  get currentSession(): string {
    return this.sessionId
  }

  async sync(sessionId: string): Promise<void> {
    if (this.sessionId === sessionId && this.container) {
      this.broadcastSnapshot()
      return
    }
    this.destroy()
    this.sessionId = sessionId
    if (!sessionId) return
    let data: { extensions: ExtensionHostState[]; enabled: boolean }
    try {
      data = await extClient.listEnabledExtensions()
    } catch (error) {
      console.warn('[extensions] load failed', error)
      return
    }
    if (!data.enabled || data.extensions.length === 0) return
    if (this.sessionId !== sessionId) return

    this.container = document.createElement('div')
    this.container.setAttribute('data-flyai-ext-host', '1')
    Object.assign(this.container.style, {
      position: 'fixed',
      left: '-10000px',
      top: '0',
      width: '1px',
      height: '1px',
      overflow: 'hidden',
      opacity: '0',
      pointerEvents: 'none',
    })
    document.body.appendChild(this.container)
    window.addEventListener('message', this.onMessage)

    const snapshot = buildSnapshot(sessionId)
    for (const state of data.extensions) {
      this.mountOne(state, snapshot)
    }
    this.watchStore()
  }

  private mountOne(state: ExtensionHostState, snapshot: ExtensionSnapshot): void {
    if (!state.js || !this.container) return
    const mounted: Mounted = {
      state,
      iframe: document.createElement('iframe'),
      ready: false,
      dead: false,
      settings: { ...(state.settings || {}) },
      commands: new Set(),
      errors: [],
    }
    mounted.iframe.setAttribute('sandbox', 'allow-scripts')
    mounted.iframe.setAttribute('aria-hidden', 'true')
    mounted.iframe.style.display = 'block'
    mounted.iframe.style.width = '1px'
    mounted.iframe.style.height = '1px'
    mounted.iframe.srcdoc = buildExtensionDoc(state, snapshot, location.origin)
    this.container.appendChild(mounted.iframe)
    this.mounted.set(state.name, mounted)
  }

  private watchStore(): void {
    if (this.unsub) return
    this.lastActive = useChatStore.getState().activeId || ''
    this.unsub = useChatStore.subscribe((state) => {
      if (state.activeId && state.activeId !== this.lastActive) {
        this.lastActive = state.activeId
        this.emitAll('CHAT_CHANGED', { chatId: state.activeId })
        this.broadcastSnapshot()
      }
      const streaming = Object.keys(state.streaming || {}).length > 0
      if (streaming !== this.lastStreaming) {
        this.lastStreaming = streaming
        this.emitAll(streaming ? 'GENERATION_STARTED' : 'GENERATION_ENDED', {})
      }
      for (const [id, session] of Object.entries(state.sessionCache)) {
        const count = session.messages.length
        const previous = this.lastCounts.get(id) || 0
        if (count !== previous) {
          this.lastCounts.set(id, count)
          if (count > previous && id === this.sessionId) {
            const latest = session.messages[count - 1] as unknown as Record<string, unknown>
            const isUser = latest?.role === 'user'
            this.emitAll(isUser ? 'MESSAGE_SENT' : 'MESSAGE_RECEIVED', {
              messageId: String(latest?.id || ''),
              text: messageText(latest?.content).slice(0, 4000),
            })
          }
        }
      }
    })
  }

  private broadcastSnapshot(): void {
    for (const item of this.mounted.values()) {
      if (!item.ready) continue
      this.emitTo(item, 'CHAT_CHANGED', { chatId: this.sessionId })
    }
  }

  private onMessage = (event: MessageEvent): void => {
    const data = event.data
    if (!isExtMessage(data) || messageTooLarge(data)) return
    const mounted = [...this.mounted.values()].find((item) => item.iframe.contentWindow === event.source)
    if (!mounted) return
    if (data.type === 'ready') {
      mounted.ready = true
      this.sendTo(mounted, 'settings', undefined, { settings: mounted.settings })
      this.emitTo(mounted, 'EXTENSIONS_FIRST_LOAD', {})
      this.emitTo(mounted, 'SETTINGS_LOADED', {})
      const activation = mounted.state.manifest.hooks?.enable
      void activation
      return
    }
    if (data.type === 'log') {
      const text = String((data as ExtMessage & { message?: string }).message || '')
      if (text) {
        mounted.errors.push(text)
        if (mounted.errors.length > 50) mounted.errors.shift()
        if ((data as { error?: boolean }).error) {
          console.warn('[ext]', mounted.state.name, text)
          useExtensionErrors.getState().add(mounted.state.name, text)
        }
      }
      return
    }
    if (data.type === 'rpc-result' || data.type === 'rpc') {
      if (data.type === 'rpc-result') {
        const pending = data.id ? this.pending.get(data.id) : undefined
        if (!pending) return
        this.pending.delete(data.id as string)
        clearTimeout(pending.timer)
        if (data.error) pending.reject(new Error(data.error.message || 'RPC failed'))
        else pending.resolve(data.result)
        return
      }
      void this.handleRpc(mounted, data)
    }
  }

  private async handleRpc(mounted: Mounted, message: ExtMessage): Promise<void> {
    const method = String(message.method || '')
    const params = (message.params || {}) as Record<string, unknown>
    const reply = (result?: unknown, error?: { message: string }): void => {
      this.sendTo(mounted, 'rpc-result', message.id, error ? { error } : { result })
    }
    if (!hasCapability(mounted.state.capabilities, method)) {
      reply(undefined, { message: `capability denied: ${method}` })
      return
    }
    try {
      switch (method) {
        case 'settings.get':
          reply({ settings: mounted.settings })
          return
        case 'settings.save': {
          const settings = (params.settings || {}) as Record<string, unknown>
          mounted.settings = settings
          await extClient.putExtensionSettings(mounted.state.name, settings)
          reply({ ok: true })
          return
        }
        case 'storage.get':
        case 'storage.set':
        case 'storage.remove':
          reply({ ok: true })
          return
        case 'variables.get': {
          const snapshot = buildSnapshot(this.sessionId)
          const type = String(params.type || 'chat')
          const messageId = Number(params.message_id)
          if (type === 'message' && Number.isFinite(messageId)) {
            const msg = snapshot.chat[messageId]
            reply({ variables: (msg?.data as { stat_data?: unknown })?.stat_data || {} })
          } else {
            reply({ variables: snapshot.variables })
          }
          return
        }
        case 'variables.set': {
          const value = (params.variables || params.value || {}) as Record<string, unknown>
          await writeSessionMvu(this.sessionId, { mode: 'replace', stat_data: value })
          reply({ ok: true })
          return
        }
        case 'chat.getMessages': {
          const snapshot = buildSnapshot(this.sessionId)
          reply({ messages: snapshot.chat, chatId: this.sessionId })
          return
        }
        case 'chat.addMessage':
        case 'chat.setMessages':
        case 'chat.deleteMessages':
          reply(undefined, { message: 'chat writes are not supported in L1 host yet' })
          return
        case 'chat.save':
          reply({ ok: true })
          return
        case 'chat.saveMetadata':
          reply({ ok: true })
          return
        case 'prompt.setExtension':
          reply({ ok: false, reason: 'extension prompt injection is not wired yet' })
          return
        case 'generate.quiet':
        case 'generate.raw': {
          const messages = Array.isArray(params.messages) ? params.messages : []
          const quiet = String(params.quiet_prompt || params.prompt || params.quietPrompt || '')
          const body: Record<string, unknown> = { purpose: 'extension', max_tokens: Number(params.max_tokens) || 0 }
          if (messages.length) body.messages = messages
          else body.messages = [
            { role: 'system', content: String(params.system_prompt || '') },
            { role: 'user', content: quiet || '请生成一段文本。' },
          ]
          const result = await request<{ text?: string; content?: string }>(
            `/api/sessions/${encodeURIComponent(this.sessionId)}/author/generate`,
            { method: 'POST', body: JSON.stringify(body) }
          )
          reply({ text: result.text || result.content || '', result })
          return
        }
        case 'worldbook.list': {
          const worlds = await listWorlds()
          reply({ worlds: worlds.map((world) => ({ id: world.id, name: world.name })) })
          return
        }
        case 'worldbook.get': {
          const id = String(params.id || params.world_id || params.name || '')
          const world = await getWorld(id)
          reply({ world })
          return
        }
        case 'ui.toast': {
          showToast(String(params.message || ''))
          reply({ ok: true })
          return
        }
        case 'ui.popup': {
          const ok = window.confirm(String(params.message || ''))
          reply({ result: ok ? 1 : 0 })
          return
        }
        case 'net.fetch': {
          const result = await proxyNet({
            method: (params.method as 'GET' | 'HEAD' | 'POST') || 'GET',
            url: String(params.url || ''),
            headers: (params.headers as Record<string, string>) || {},
            body_base64: params.body ? b64(String(params.body)) : undefined,
          })
          reply({ status: result.status, headers: result.headers, text: result.body_base64 ? unb64(result.body_base64) : '' })
          return
        }
        case 'http.fetch': {
          const url = new URL(String(params.url || ''), location.href)
          if (url.origin !== location.origin) throw new Error('cross-origin fetch must use net.fetch')
          const response = await fetch(url.href, {
            method: String(params.method || 'GET'),
            headers: (params.headers as Record<string, string>) || {},
            body: params.body ? String(params.body) : undefined,
          })
          const text = (await response.text()).slice(0, 2 * 1024 * 1024)
          reply({ status: response.status, text })
          return
        }
        case 'template.render': {
          const base = mounted.state.baseUrl
          const template = String(params.template || '')
          const response = await fetch(`${base}${template}`)
          let text = await response.text()
          const args = (params.args || {}) as Record<string, unknown>
          text = text.replace(/\{\{([^}]+)\}\}/g, (_, key) => String(args[String(key).trim()] ?? ''))
          reply({ html: text })
          return
        }
        case 'character.writeField':
          reply(undefined, { message: 'character.writeField is not supported yet' })
          return
        case 'ui.widget.register': {
          const slot = String(params.slot || 'message') === 'header' ? 'header' : 'message'
          useExtensionWidgets.getState().register({
            ext: mounted.state.name,
            slot,
            id: String(params.id || ''),
            label: String(params.label || params.id || ''),
            title: String(params.title || params.label || ''),
            command: String(params.command || ''),
          })
          reply({ ok: true })
          return
        }
        case 'slash.register': {
          const name = String(params.name || '').replace(/^\//, '').toLowerCase()
          if (name) {
            slashCommands.set(name, {
              name,
              help: String(params.help || ''),
              ext: mounted.state.name,
            })
            mounted.commands.add(name)
          }
          reply({ ok: true })
          return
        }
        case 'slash.execute': {
          const runner = (mounted.iframe.contentWindow as unknown as { __FLYAI_EXT_HOST__?: { executeSlash?: (text: string) => Promise<unknown> } })?.__FLYAI_EXT_HOST__
          if (!runner?.executeSlash) throw new Error('slash runtime missing')
          const result = await runner.executeSlash(String(params.text || ''))
          reply({ result })
          return
        }
        default:
          reply(undefined, { message: `unknown method: ${method}` })
      }
    } catch (error) {
      reply(undefined, { message: error instanceof Error ? error.message : String(error) })
    }
  }

  private sendTo(mounted: Mounted, type: ExtMessage['type'], id?: string, payload?: Record<string, unknown>): void {
    try {
      mounted.iframe.contentWindow?.postMessage(
        { v: EXT_VERSION, ns: EXT_NS, type, id, ...(payload || {}) },
        '*'
      )
    } catch {
      // iframe 已销毁
    }
  }

  private emitTo(mounted: Mounted, event: string, payload: unknown): void {
    this.sendTo(mounted, 'event', undefined, { event, payload })
  }

  private emitAll(event: string, payload: unknown): void {
    for (const mounted of this.mounted.values()) {
      if (mounted.ready) this.emitTo(mounted, event, payload)
    }
  }

  async runWidget(widget: { ext: string; command: string }): Promise<void> {
    if (!widget.command) return
    const mounted = this.mounted.get(widget.ext)
    if (!mounted?.ready) return
    try {
      const result = await this.call(mounted, 'slash.execute', { text: widget.command })
      const value = (result as { result?: unknown })?.result
      if (value !== undefined && value !== null && String(value).length > 0) showToast(String(value))
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error))
    }
  }

  async executeSlash(text: string): Promise<boolean> {
    const name = text.trim().split(/\s+/)[0].replace(/^\//, '').toLowerCase()
    const command = slashCommands.get(name)
    if (!command) return false
    const mounted = this.mounted.get(command.ext)
    if (!mounted?.ready) return false
    try {
      const result = await this.call(mounted, 'slash.execute', { text })
      const value = (result as { result?: unknown })?.result
      if (value !== undefined && value !== null && String(value).length > 0) showToast(String(value))
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error))
    }
    return true
  }

  private call(mounted: Mounted, method: string, params: Record<string, unknown>): Promise<unknown> {
    const id = `c${++this.seq}`
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`RPC timeout: ${method}`))
      }, 15000)
      this.pending.set(id, { resolve, reject, timer })
      this.sendTo(mounted, 'rpc', id, { method, params })
    })
  }

  destroy(): void {
    if (this.unsub) {
      this.unsub()
      this.unsub = null
    }
    window.removeEventListener('message', this.onMessage)
    for (const item of this.mounted.values()) {
      item.dead = true
      try {
        item.iframe.remove()
      } catch {
        // ignore
      }
    }
    this.mounted.clear()
    useExtensionWidgets.getState().clearAll()
    useExtensionErrors.getState().clearAll()
    for (const pending of this.pending.values()) window.clearTimeout(pending.timer)
    this.pending.clear()
    slashCommands.clear()
    if (this.container) {
      this.container.remove()
      this.container = null
    }
    this.sessionId = ''
  }
}

export const extensionHost = new ExtensionHostManager()

/** 聊天输入框 `/cmd` 入口：命中扩展注册的命令时接管发送。 */
export const executeExtensionSlash = async (text: string, sessionId: string): Promise<boolean> => {
  if (extensionHost.currentSession !== sessionId) {
    await extensionHost.sync(sessionId)
  }
  return extensionHost.executeSlash(text)
}

export const listExtensionSlashCommands = (): string[] => [...slashCommands.keys()]
