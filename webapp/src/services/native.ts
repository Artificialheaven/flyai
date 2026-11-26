/**
 * Capacitor 原生桥（只在 APK 内生效，Web 端全部为 no-op）。
 *
 * - NativeSaver：blob → base64 → FlySaver，写入系统「下载」目录（WebView 不接管
 *   `<a download>`，APK 里不桥接就会点击无反应）。
 * - NativeNotify：App 切后台时由 FlyNotify 原生线程轮询 run 状态，结束后发本地通知；
 *   回到前台即取消轮询（前台由页面自己展示流式回复，不打扰）。
 * - 返回键：交给调用方处理（弹窗/抽屉/会话），未消费时退出应用。
 *
 * 直接读 window.Capacitor.Plugins，避免 Web 构建引入 @capacitor 依赖；
 * 插件未安装或非原生环境时所有函数静默跳过。
 */
import { getApiBase } from './flyai/runtimeConfig.js'

type NativeMethod = (...args: unknown[]) => unknown
type NativePlugin = Record<string, NativeMethod | undefined>

type CapacitorGlobal = {
  isNativePlatform?: () => boolean
  getPlatform?: () => string
  Plugins?: Record<string, NativePlugin | undefined>
}

const capacitor = (): CapacitorGlobal | undefined => {
  if (typeof window === 'undefined') {
    return undefined
  }
  return (window as unknown as { Capacitor?: CapacitorGlobal }).Capacitor
}

/** 是否运行在 APK（Capacitor 原生壳）内 */
export const isNativeApp = (): boolean => {
  const bridge = capacitor()
  return bridge?.isNativePlatform?.() === true || bridge?.getPlatform?.() === 'android'
}

const plugin = (name: string): NativePlugin | undefined => capacitor()?.Plugins?.[name]

const blobToBase64 = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : '')
    }
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(blob)
  })

const anchorDownload = (href: string, filename: string) => {
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

/** 浏览器下载（blob 地址需要及时释放） */
const webDownloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob)
  anchorDownload(url, filename)
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * POC 裸 WebView 没有 Capacitor：下载、后台完成通知、状态栏颜色都走
 * 主页面的 FlyAIPoc 原生桥（由 MainActivity 注入）。
 */
type PocAppBridge = {
  saveUrl?: (token: string, url: string, name: string) => string
  saveBase64?: (token: string, data: string, name: string, mime: string) => string
  watchRun?: (
    token: string,
    runId: string,
    statusUrl: string,
    title: string,
    body: string,
    iconUrl: string
  ) => string
  cancelWatch?: (token: string) => string
  setStatusBar?: (token: string, color: string, dark: string) => string
}

const pocBridge = (): { token: string; bridge: PocAppBridge } | null => {
  if (typeof window === 'undefined') {
    return null
  }
  const win = window as unknown as {
    __flyaiPoc?: { token?: string }
    FlyAIPoc?: PocAppBridge
  }
  const token = win.__flyaiPoc?.token
  if (!token || !win.FlyAIPoc) {
    return null
  }
  return { token, bridge: win.FlyAIPoc }
}

/** 当前环境是否支持原生保存（Capacitor FlySaver 或 POC 下载桥） */
export const canNativeSave = (): boolean => {
  if (isNativeApp()) {
    return true
  }
  const poc = pocBridge()
  return Boolean(poc?.bridge.saveUrl || poc?.bridge.saveBase64)
}

const bridgeSaved = (raw: string | null | undefined): boolean => {
  if (!raw) {
    return false
  }
  try {
    return (JSON.parse(raw) as { ok?: boolean }).ok === true
  } catch {
    return false
  }
}

/**
 * 保存文件：APK 内交给 FlySaver（Android 10+ 走 MediaStore，无需存储权限），
 * 其余环境走浏览器下载。返回是否成功（调用方据此提示失败）。
 */
export const downloadBlob = async (
  blob: Blob,
  filename: string,
  mime?: string
): Promise<boolean> => {
  const saver = plugin('FlySaver')
  if (isNativeApp() && saver?.save) {
    try {
      await saver.save({
        data: await blobToBase64(blob),
        name: filename,
        mime: mime || blob.type || 'application/octet-stream',
      })
      return true
    } catch {
      return false
    }
  }
  const poc = pocBridge()
  if (poc?.bridge.saveBase64) {
    try {
      const data = await blobToBase64(blob)
      return bridgeSaved(
        poc.bridge.saveBase64(
          poc.token,
          data,
          filename,
          mime || blob.type || 'application/octet-stream'
        )
      )
    } catch {
      return false
    }
  }
  webDownloadBlob(blob, filename)
  return true
}

/** 下载远程资源（服务端图片/语音）：APK 内先取回再交给 FlySaver */
export const downloadUrl = async (url: string, filename: string): Promise<boolean> => {
  const poc = pocBridge()
  if (poc?.bridge.saveUrl) {
    try {
      if (/^(data|blob):/i.test(url)) {
        // data/blob URL 无法交给原生网络层，转 blob 走 base64 通道
        const res = await fetch(url)
        return await downloadBlob(await res.blob(), filename)
      }
      const absolute = new URL(url, window.location.href).href
      return bridgeSaved(poc.bridge.saveUrl(poc.token, absolute, filename))
    } catch {
      return false
    }
  }
  if (!isNativeApp()) {
    anchorDownload(url, filename)
    return true
  }
  try {
    const res = await fetch(url)
    if (!res.ok) {
      return false
    }
    return await downloadBlob(await res.blob(), filename)
  } catch {
    return false
  }
}

export type RunWatch = {
  runId: string
  /** 通知标题（会话角色名） */
  title: string
  /** 通知正文（已本地化） */
  body: string
  /** 登录令牌：原生线程轮询状态用 */
  token: string
  /** 角色头像绝对地址，缺省时用默认小图标 */
  iconUrl?: string
}

/** 当前应在后台结束后提醒的运行；同一时刻只保留最新一轮 */
let pendingWatch: RunWatch | null = null
/** 原生侧是否已在轮询 */
let watchActive = false
/** App 是否在前台；只有切后台才需要原生轮询兜底 */
let appActive = true

const runStatusUrl = (runId: string): string => {
  const path = `/api/chat/runs/${encodeURIComponent(runId)}`
  try {
    return new URL(`${getApiBase()}${path}`, window.location.href).href
  } catch {
    return ''
  }
}

const startWatch = () => {
  // POC：没有 Capacitor appStateChange，注册即交给原生轮询；
  // 原生在完成时判断前后台，前台完成不打扰。
  const poc = pocBridge()
  if (poc?.bridge.watchRun && pendingWatch) {
    const statusUrl = runStatusUrl(pendingWatch.runId)
    if (!statusUrl) {
      return
    }
    let iconUrl = pendingWatch.iconUrl ?? ''
    if (iconUrl) {
      try {
        iconUrl = new URL(iconUrl, window.location.href).href
      } catch {
        iconUrl = ''
      }
    }
    watchActive = true
    poc.bridge.watchRun(
      poc.token,
      pendingWatch.runId,
      statusUrl,
      pendingWatch.title,
      pendingWatch.body,
      iconUrl
    )
    return
  }
  const notify = plugin('FlyNotify')
  if (!isNativeApp() || appActive || watchActive || !pendingWatch || !notify?.watchRun) {
    return
  }
  const statusUrl = runStatusUrl(pendingWatch.runId)
  if (!statusUrl) {
    return
  }
  watchActive = true
  void Promise.resolve(
    notify.watchRun({
      runId: pendingWatch.runId,
      statusUrl,
      token: pendingWatch.token,
      title: pendingWatch.title,
      body: pendingWatch.body,
      iconUrl: pendingWatch.iconUrl ?? '',
    })
  ).catch(() => {
    watchActive = false
  })
}

const stopWatch = () => {
  watchActive = false
  const poc = pocBridge()
  if (poc?.bridge.cancelWatch) {
    poc.bridge.cancelWatch(poc.token)
    return
  }
  const notify = plugin('FlyNotify')
  if (notify?.cancelWatch) {
    void Promise.resolve(notify.cancelWatch({})).catch(() => {})
  }
}

/**
 * 记录本轮运行：进入后台时原生轮询等它结束；传 null 清除并取消轮询。
 * 清除时可带 runId，只清自己那一轮（同时有别的会话在跑时不误伤）。
 */
export const setRunWatch = (watch: RunWatch | null, runId?: string) => {
  if (!watch && runId && pendingWatch && pendingWatch.runId !== runId) {
    return
  }
  pendingWatch = watch
  if (!watch) {
    stopWatch()
    return
  }
  startWatch()
}

export type NativeAppInfo = {
  /** versionName，如 1.27.6134 */
  version: string
  /** versionCode；Capacitor App 插件以字符串返回，缺失时为空串 */
  build: string
}

export type UpdateInstallCheck = {
  /** 是否已允许安装未知来源应用 */
  allowed: boolean
  /** Android API 等级；0 表示插件不可用 */
  sdk: number
}

export type UpdateProgress = {
  loaded: number
  /** 服务端未给 Content-Length 时为 0 */
  total: number
  percent: number
}

export type UpdateDownloadResult = {
  fileName: string
  bytes: number
}

/** 当前安装包的版本信息；非原生环境或插件缺失返回 null */
export const getNativeAppInfo = async (): Promise<NativeAppInfo | null> => {
  const app = plugin('App')
  if (!isNativeApp() || !app?.getInfo) {
    return null
  }
  try {
    const info = (await app.getInfo({})) as { version?: unknown; build?: unknown } | undefined
    return {
      version: typeof info?.version === 'string' ? info.version : '',
      build: typeof info?.build === 'string' ? info.build : String(info?.build ?? ''),
    }
  } catch {
    return null
  }
}

/** 是否允许安装未知来源应用（Android 8+ 需要用户在系统设置里授权） */
export const updateCanInstall = async (): Promise<UpdateInstallCheck> => {
  const updater = plugin('FlyUpdater')
  if (!isNativeApp() || !updater?.canInstall) {
    return { allowed: false, sdk: 0 }
  }
  try {
    const result = (await updater.canInstall({})) as
      | { allowed?: unknown; sdk?: unknown }
      | undefined
    return {
      allowed: result?.allowed === true,
      sdk: typeof result?.sdk === 'number' ? result.sdk : 0,
    }
  } catch {
    return { allowed: false, sdk: 0 }
  }
}

/** 打开本应用的「安装未知应用」授权页 */
export const openUpdateInstallSettings = async (): Promise<void> => {
  const updater = plugin('FlyUpdater')
  if (!isNativeApp() || !updater?.openInstallSettings) {
    return
  }
  try {
    await updater.openInstallSettings({})
  } catch {
    // 系统设置页打不开时保持原状，由调用方提示
  }
}

/** 原生后台下载 APK 到缓存目录；进度走 onUpdateProgress */
export const downloadAppUpdate = async (
  url: string,
  fileName: string
): Promise<UpdateDownloadResult> => {
  const updater = plugin('FlyUpdater')
  if (!isNativeApp() || !updater?.download) {
    throw new Error('FlyUpdater unavailable')
  }
  const result = (await updater.download({ url, fileName })) as
    | { fileName?: unknown; bytes?: unknown }
    | undefined
  return {
    fileName: typeof result?.fileName === 'string' ? result.fileName : fileName,
    bytes: typeof result?.bytes === 'number' ? result.bytes : 0,
  }
}

/** 调起系统安装器；失败时 reject 的 error 带 code（UNKNOWN_SOURCES / NOT_DOWNLOADED） */
export const installAppUpdate = async (fileName: string): Promise<void> => {
  const updater = plugin('FlyUpdater')
  if (!isNativeApp() || !updater?.install) {
    throw new Error('FlyUpdater unavailable')
  }
  await updater.install({ fileName })
}

/** 订阅下载进度，返回取消订阅函数；非原生环境返回空操作 */
export const onUpdateProgress = (cb: (progress: UpdateProgress) => void): (() => void) => {
  const updater = plugin('FlyUpdater')
  if (!isNativeApp() || !updater?.addListener) {
    return () => {}
  }
  let cancelled = false
  let remove: (() => void) | null = null
  const detach = () => {
    if (remove) {
      const fn = remove
      remove = null
      fn()
    }
  }
  void Promise.resolve(
    updater.addListener('downloadProgress', (event?: Record<string, unknown>) => {
      if (cancelled) {
        return
      }
      const data = event ?? {}
      cb({
        loaded: typeof data.loaded === 'number' ? data.loaded : 0,
        total: typeof data.total === 'number' ? data.total : 0,
        percent: typeof data.percent === 'number' ? data.percent : 0,
      })
    })
  )
    .then((handle) => {
      const fn = (handle as { remove?: () => void } | undefined)?.remove
      if (typeof fn === 'function') {
        remove = () => fn.call(handle)
      }
      if (cancelled) {
        detach()
      }
    })
    .catch(() => {})
  return () => {
    cancelled = true
    detach()
  }
}

let initialized = false

/** POC 的裸 WebView 没有 Capacitor，用全局函数承接原生返回键 */
type NativeBackWindow = {
  __flyaiNativeBack?: () => boolean
  __flyaiNativeBackDown?: () => void
}

/**
 * 初始化原生桥：App 前后台切（驱动通知轮询）、返回键、通知权限。
 * Capacitor 插件只首次调用生效；`onBack` 返回 true 表示已消费返回键。
 * POC 裸 WebView 没有 Capacitor，仍会挂上全局返回钩子供 MainActivity 调用。
 */
export const initNative = (onBack: () => boolean) => {
  // 无论 Capacitor 是否存在都挂上：POC WebView 由 MainActivity 直接调用。
  // 按下/松开分开暴露，原生层可以在按住时先吃掉系统默认行为。
  if (typeof window !== 'undefined') {
    const bridge = window as unknown as NativeBackWindow
    bridge.__flyaiNativeBack = onBack
    bridge.__flyaiNativeBackDown = () => {}
  }
  if (initialized || !isNativeApp()) {
    return
  }
  initialized = true
  const app = plugin('App')
  if (app?.addListener) {
    const addListener = app.addListener
    void Promise.resolve(
      addListener('appStateChange', (state?: Record<string, unknown>) => {
        appActive = state?.isActive !== false
        if (appActive) {
          stopWatch()
        } else {
          startWatch()
        }
      })
    ).catch(() => {})
    void Promise.resolve(
      addListener('backButton', () => {
        if (!onBack()) {
          const exitApp = app.exitApp
          if (exitApp) {
            void Promise.resolve(exitApp({})).catch(() => {})
          }
        }
      })
    ).catch(() => {})
  }
  // Android 13+ 的通知运行时权限：LocalNotifications 申请，授权对全应用生效
  // （FlyNotify 只检查权限，不负责弹窗）。
  const notifications = plugin('LocalNotifications')
  if (notifications?.requestPermissions) {
    void Promise.resolve(notifications.requestPermissions({})).catch(() => {})
  }
}