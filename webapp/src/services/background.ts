import { create } from 'zustand'
import { getMyBackground, putMyBackground } from './flyai/admin.js'
import type {
  BackgroundMode,
  BackgroundSettings,
  DeviceBackground,
} from './flyai/admin.js'
import { getStoredUid, useAuth } from './flyai/auth.js'
import { deviceKind } from './device.js'
import type { DeviceKind } from './device.js'

/**
 * 应用背景状态（云端随账号同步，localStorage 按 uid 缓存）。
 *
 * - 首屏先同步播种缓存，再异步拉服务端，避免刷新时背景闪一下；
 * - PUT 为补丁语义，保存失败回滚到上一次值并抛错给 UI；
 * - body[data-app-bg] 是半透明表面变量的开关，theme 模式下移除。
 */

export const DEFAULT_DEVICE_BG: DeviceBackground = {
  mode: 'theme',
  color: '',
  image: '',
  opacity: 100,
}

const MODES: BackgroundMode[] = ['theme', 'solid', 'image']
const COLOR_RE = /^#[0-9a-fA-F]{6}$/
const IMAGE_RE = /^\/uploads\/[A-Za-z0-9_-]{1,64}\/[A-Za-z0-9_-]{1,96}\.(png|jpe?g|gif|webp)$/i

type BackgroundState = {
  desktop: DeviceBackground
  mobile: DeviceBackground
  loaded: boolean
}

export const useBackground = create<BackgroundState>(() => ({
  desktop: { ...DEFAULT_DEVICE_BG },
  mobile: { ...DEFAULT_DEVICE_BG },
  loaded: false,
}))

const cacheKey = (uid: string) => 'chat-demo:background:' + uid

/** 客户端归一化：与后端保持同一套规则，损坏数据按默认值处理 */
/** 与后端一致：非数字回 100，越界钳制到 0-100 */
const normalizeOpacity = (value: unknown): number => {
  const number = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(number)) {
    return 100
  }
  return Math.max(0, Math.min(100, Math.round(number)))
}

const normalizeDevice = (raw: unknown): DeviceBackground => {
  if (!raw || typeof raw !== 'object') {
    return { ...DEFAULT_DEVICE_BG }
  }
  const data = raw as Partial<DeviceBackground>
  let mode: BackgroundMode = MODES.includes(data.mode as BackgroundMode)
    ? (data.mode as BackgroundMode)
    : 'theme'
  const color =
    typeof data.color === 'string' && COLOR_RE.test(data.color)
      ? data.color.toLowerCase()
      : ''
  const image =
    typeof data.image === 'string' && IMAGE_RE.test(data.image) ? data.image : ''
  if (mode === 'solid' && !color) {
    mode = 'theme'
  }
  if (mode === 'image' && !image) {
    mode = 'theme'
  }
  return { mode, color, image, opacity: normalizeOpacity(data.opacity) }
}

const normalizeSettings = (data: BackgroundSettings): BackgroundSettings => ({
  desktop: normalizeDevice(data && data.desktop),
  mobile: normalizeDevice(data && data.mobile),
})

let currentUid: string | null = null

const resolveUid = (): string | null =>
  currentUid ?? useAuth.getState().user?.id ?? getStoredUid()

const writeCache = (uid: string | null, state: BackgroundState) => {
  if (!uid) {
    return
  }
  try {
    localStorage.setItem(
      cacheKey(uid),
      JSON.stringify({ desktop: state.desktop, mobile: state.mobile })
    )
  } catch {
    // 隐私模式/配额满：缓存只是提速手段，失败不影响功能
  }
}

const persistCache = () => {
  writeCache(resolveUid(), useBackground.getState())
}

/** 同步播种本机缓存（调用方保证已登录） */
export const seedFromCache = (uid: string) => {
  currentUid = uid
  try {
    const raw = localStorage.getItem(cacheKey(uid))
    if (!raw) {
      return
    }
    const parsed = JSON.parse(raw) as Partial<BackgroundSettings>
    const normalized = normalizeSettings(parsed as BackgroundSettings)
    useBackground.setState({ desktop: normalized.desktop, mobile: normalized.mobile })
    applyBackgroundPresentation()
  } catch {
    // 缓存损坏：等接口返回
  }
}

/** 登录后调用：先缓存播种，再拉服务端；失败静默，不打扰启动 */
export const loadBackground = async (): Promise<void> => {
  const uid = resolveUid()
  currentUid = uid
  if (!uid) {
    clearBackground()
    return
  }
  seedFromCache(uid)
  try {
    const data = normalizeSettings(await getMyBackground())
    useBackground.setState({ desktop: data.desktop, mobile: data.mobile, loaded: true })
    persistCache()
    applyBackgroundPresentation()
  } catch {
    // 断网/旧服务器：继续用缓存或默认值
  }
}

type DevicePatch = {
  mode?: BackgroundMode
  color?: string
  image?: string
  opacity?: number
}

/** 仅更新内存与缓存：颜色拖动、上传完成后的即时预览用 */
export const setLocalBackground = (device: DeviceKind, patch: DevicePatch) => {
  const state = useBackground.getState()
  const next = normalizeDevice({ ...state[device], ...patch })
  useBackground.setState(device === 'desktop' ? { desktop: next } : { mobile: next })
  persistCache()
  applyBackgroundPresentation()
}

/** 每个设备一条保存序列：并发保存时只有最后一次的响应/回滚生效 */
const saveSeq: Record<DeviceKind, number> = { desktop: 0, mobile: 0 }

/**
 * 保存到服务端：先乐观更新，失败回滚到保存前的值并抛出，由界面提示。
 * 服务端返回的归一化结果会覆盖本地（例如清空图片自动回落 theme）。
 */
export const saveDeviceBackground = async (
  device: DeviceKind,
  patch: DevicePatch
): Promise<BackgroundSettings> => {
  const state = useBackground.getState()
  const previous = state[device]
  const optimistic = normalizeDevice({ ...previous, ...patch })
  useBackground.setState(device === 'desktop' ? { desktop: optimistic } : { mobile: optimistic })
  persistCache()
  applyBackgroundPresentation()

  const seq = ++saveSeq[device]
  try {
    const data = normalizeSettings(
      await putMyBackground({ device, ...patch })
    )
    if (seq === saveSeq[device]) {
      useBackground.setState({
        desktop: data.desktop,
        mobile: data.mobile,
        loaded: true,
      })
      persistCache()
      applyBackgroundPresentation()
    }
    return data
  } catch (error) {
    if (seq === saveSeq[device]) {
      useBackground.setState(device === 'desktop' ? { desktop: previous } : { mobile: previous })
      persistCache()
      applyBackgroundPresentation()
    }
    throw error
  }
}

/** 登出/未登录：恢复默认并关闭半透明表面 */
export const clearBackground = () => {
  currentUid = null
  useBackground.setState({
    desktop: { ...DEFAULT_DEVICE_BG },
    mobile: { ...DEFAULT_DEVICE_BG },
    loaded: false,
  })
  applyBackgroundPresentation()
}

/** 当前设备应使用的背景 */
export const activeBackground = (): DeviceBackground =>
  useBackground.getState()[deviceKind]

/** 设置 body[data-app-bg]，开启/关闭 CSS 里的半透明表面变量 */
export const applyBackgroundPresentation = () => {
  if (typeof document === 'undefined') {
    return
  }
  const bg = activeBackground()
  if (bg.mode === 'theme') {
    delete document.body.dataset.appBg
  } else {
    document.body.dataset.appBg = bg.mode
  }
}
