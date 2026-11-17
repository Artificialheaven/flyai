/**
 * APK 自动更新检查。
 *
 * 服务端扫描自己的 webroot，取最高的 FlyAI-x.xx.xxxx.apk 作为最新版本；
 * 客户端拿本地 versionCode 与 /api/app/version 的 version_code 比较，更高才弹窗。
 * 只在 APK 内生效；离线、超时、老服务端（404）一律静默，绝不阻塞启动。
 */
import { create } from 'zustand'
import { getNativeAppInfo, isNativeApp, type NativeAppInfo } from '../native.js'
import { apiUrl } from './client.js'

export type AppUpdateInfo = {
  version: string
  versionCode: number
  fileName: string
  url: string
  size: number
  currentVersion: string
}

type AppUpdateState = {
  open: boolean
  update: AppUpdateInfo | null
}

export const useAppUpdate = create<AppUpdateState>(() => ({ open: false, update: null }))

export const dismissAppUpdate = () => useAppUpdate.setState({ open: false })

/** 与后端 updates.py 相同的公式；非法版本名返回 0 */
export const versionCodeFromName = (version: string): number => {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version.trim())
  if (!match) {
    return 0
  }
  const major = Number(match[1])
  const minor = Number(match[2])
  const revision = Number(match[3])
  if (minor > 99 || revision > 9999) {
    return 0
  }
  return major * 1_000_000 + minor * 10_000 + revision
}

const localVersionCode = (info: NativeAppInfo): number => {
  const build = Number.parseInt(info.build, 10)
  if (Number.isFinite(build) && build > 0) {
    return build
  }
  return versionCodeFromName(info.version)
}

type RemoteVersion = {
  version: string | null
  versionCode: number | null
  fileName: string | null
  url: string | null
  size: number
}

const REQUEST_TIMEOUT_MS = 8000

const fetchRemoteVersion = async (): Promise<RemoteVersion | null> => {
  // 不用 AbortSignal.timeout：兼容旧 WebView
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(apiUrl('/api/app/version'), {
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!res.ok) {
      return null
    }
    const data = (await res.json()) as Record<string, unknown>
    return {
      version: typeof data.version === 'string' ? data.version : null,
      versionCode: typeof data.version_code === 'number' ? data.version_code : null,
      fileName: typeof data.file_name === 'string' ? data.file_name : null,
      url: typeof data.url === 'string' ? data.url : null,
      size: typeof data.size === 'number' ? data.size : 0,
    }
  } catch {
    return null
  } finally {
    window.clearTimeout(timer)
  }
}

/** 服务端返回的是相对地址；按当前 apiBase 拼绝对地址（兼容反代/CDN） */
export const absoluteApkUrl = (url: string): string => {
  if (/^https?:\/\//i.test(url)) {
    return url
  }
  return apiUrl(encodeURI(url.startsWith('/') ? url : `/${url}`))
}

/** 本次 App 会话已提示过；「稍后」后不再打扰，下次启动重新检查 */
let promptedThisSession = false
let checking = false

/** 启动时调用一次；内部吞掉所有异常 */
export const checkForAppUpdate = async (): Promise<void> => {
  if (promptedThisSession || checking || !isNativeApp()) {
    return
  }
  checking = true
  try {
    const info = await getNativeAppInfo()
    if (!info) {
      return
    }
    const local = localVersionCode(info)
    if (local <= 0) {
      return
    }
    const remote = await fetchRemoteVersion()
    if (!remote || remote.versionCode === null || remote.versionCode <= local) {
      return
    }
    if (!remote.fileName || !remote.url) {
      return
    }
    promptedThisSession = true
    useAppUpdate.setState({
      open: true,
      update: {
        version: remote.version ?? remote.fileName,
        versionCode: remote.versionCode,
        fileName: remote.fileName,
        url: remote.url,
        size: remote.size,
        currentVersion: info.version || String(local),
      },
    })
  } catch {
    // 更新检查失败不打扰用户
  } finally {
    checking = false
  }
}
