/** 扩展宿主 postMessage 协议：校验、能力表与 RPC 类型。 */

export const EXT_NS = 'flyai-ext'
export const EXT_VERSION = 1
export const MAX_MESSAGE_BYTES = 256 * 1024

export type ExtMessage = {
  v: number
  ns: string
  id?: string
  type: 'boot' | 'ready' | 'rpc' | 'rpc-result' | 'event' | 'log' | 'error' | 'settings'
  method?: string
  params?: unknown
  result?: unknown
  error?: { code?: string; message?: string }
  event?: string
  payload?: unknown
  message?: string
  settings?: Record<string, unknown>
}

/** RPC 方法 → 所需能力；undefined 表示无需声明（基础能力）。 */
export const RPC_CAPABILITIES: Record<string, string | undefined> = {
  'settings.get': 'settings',
  'settings.save': 'settings',
  'storage.get': 'storage',
  'storage.set': 'storage',
  'storage.remove': 'storage',
  'chat.getMessages': 'chat.read',
  'chat.setMessages': 'chat.write',
  'chat.addMessage': 'chat.write',
  'chat.deleteMessages': 'chat.write',
  'chat.save': 'chat.write',
  'chat.saveMetadata': undefined,
  'chat.reload': undefined,
  'variables.get': 'variables.read',
  'variables.set': 'variables.write',
  'worldbook.list': 'worldbook.read',
  'worldbook.get': 'worldbook.read',
  'worldbook.edit': 'worldbook.write',
  'generate.quiet': 'generate.quiet',
  'generate.raw': 'generate.raw',
  'prompt.setExtension': 'prompt.inject',
  'ui.toast': undefined,
  'ui.popup': undefined,
  'net.fetch': 'net',
  'http.fetch': undefined,
  'slash.register': 'slash.register',
  'slash.execute': 'slash.execute',
  'template.render': undefined,
  'character.writeField': 'worldbook.write',
  log: undefined,
}

export const isExtMessage = (data: unknown): data is ExtMessage => {
  if (!data || typeof data !== 'object') return false
  const message = data as ExtMessage
  return message.ns === EXT_NS && message.v === EXT_VERSION && typeof message.type === 'string'
}

export const messageTooLarge = (data: unknown): boolean => {
  try {
    return JSON.stringify(data).length > MAX_MESSAGE_BYTES
  } catch {
    return true
  }
}

export const hasCapability = (capabilities: string[], method: string): boolean => {
  const required = RPC_CAPABILITIES[method]
  if (!required) return true
  return capabilities.includes(required)
}
