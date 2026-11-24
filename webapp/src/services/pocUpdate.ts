/**
 * POC 本地更新桥（只在 all-in-one APK 内生效）。
 *
 * 原生 MainActivity 注入 window.__flyaiPoc（token/版本/代码 rev），并暴露
 * window.FlyAIPoc 的 getState/check/downloadOverlay/downloadFull/apply/markReady。
 * 卡片 iframe 是 sandbox 跨源页面，拿不到主页面的 token，因此不能调用更新桥。
 */

export type PocInfo = {
  supported: boolean
  versionCode: number
  versionName: string
  baseRev: string
  codeRev: string
  overlayVersion: number
  pending: boolean
  pendingVersion?: string
  pendingOverlayVersion?: number
  pendingSize?: number
  error?: string
}

export type PocUpdateEvent = {
  state:
    | 'idle'
    | 'checking'
    | 'available'
    | 'downloading'
    | 'staged'
    | 'installing'
    | 'applying'
    | 'uptodate'
    | 'failed'
    | 'busy'
  kind?: 'overlay' | 'full'
  version?: string
  size?: number
  loaded?: number
  total?: number
  percent?: number
  notes?: string
  error?: string
  staged?: boolean
}

type BridgeResult = { ok?: boolean; error?: string }

type PocWindow = Window & {
  __flyaiPoc?: PocInfo & { token?: string }
  __flyaiPocEvent?: (event: PocUpdateEvent) => void
  FlyAIPoc?: {
    getState?: (token: string) => string
    check?: (token: string) => string
    downloadOverlay?: (token: string) => string
    downloadFull?: (token: string) => string
    cancel?: (token: string) => string
    applyOverlay?: (token: string) => string
    markReady?: (token: string) => string
    openInstallSettings?: (token: string) => string
  }
}

const pocWindow = (): PocWindow | null =>
  typeof window === 'undefined' ? null : (window as PocWindow)

const token = (): string => pocWindow()?.__flyaiPoc?.token ?? ''

export const isPocUpdateSupported = (): boolean => {
  const win = pocWindow()
  return Boolean(win?.FlyAIPoc?.getState && win?.__flyaiPoc?.token)
}

const call = (name: keyof NonNullable<PocWindow['FlyAIPoc']>): BridgeResult => {
  const win = pocWindow()
  const fn = win?.FlyAIPoc?.[name]
  const current = token()
  if (!fn || !current) {
    return { ok: false, error: 'unsupported' }
  }
  try {
    const raw = fn(current)
    return raw ? (JSON.parse(raw) as BridgeResult) : { ok: false, error: 'empty' }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

export const getPocInfo = (): PocInfo | null => {
  const win = pocWindow()
  const fn = win?.FlyAIPoc?.getState
  const current = token()
  if (!fn || !current) {
    return null
  }
  try {
    const raw = fn(current)
    return raw ? (JSON.parse(raw) as PocInfo) : null
  } catch {
    return null
  }
}

export const checkPocUpdate = (): BridgeResult => call('check')
export const downloadPocOverlay = (): BridgeResult => call('downloadOverlay')
export const downloadPocFullApk = (): BridgeResult => call('downloadFull')
export const cancelPocUpdate = (): BridgeResult => call('cancel')
export const applyPocOverlay = (): BridgeResult => call('applyOverlay')
export const markPocReady = (): BridgeResult => call('markReady')
export const openPocInstallSettings = (): BridgeResult => call('openInstallSettings')

const EVENT_NAME = 'flyai-poc-update'
const READY_EVENT = 'flyai-poc-ready'

/** 提前安装全局事件入口，避免原生在模块加载前回调时丢事件。 */
if (typeof window !== 'undefined') {
  const win = window as PocWindow
  win.__flyaiPocEvent = (event: PocUpdateEvent) => {
    window.dispatchEvent(new CustomEvent<PocUpdateEvent>(EVENT_NAME, { detail: event }))
  }
}

export const onPocUpdateEvent = (
  listener: (event: PocUpdateEvent) => void
): (() => void) => {
  if (typeof window === 'undefined') {
    return () => {}
  }
  const handler = (event: Event) => listener((event as CustomEvent<PocUpdateEvent>).detail)
  window.addEventListener(EVENT_NAME, handler)
  return () => window.removeEventListener(EVENT_NAME, handler)
}

/** 原生注入 window.__flyaiPoc 后触发；设置页据此刷新当前版本。 */
export const onPocReady = (listener: () => void): (() => void) => {
  if (typeof window === 'undefined') {
    return () => {}
  }
  window.addEventListener(READY_EVENT, listener)
  return () => window.removeEventListener(READY_EVENT, listener)
}
