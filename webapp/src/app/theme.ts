import { useSyncExternalStore } from 'react'
import { useSettings, type ThemePreference } from '../services/settings.js'

export type Theme = 'light' | 'dark'

const query = window.matchMedia('(prefers-color-scheme: dark)')

const subscribeOsTheme = (listener: () => void) => {
  query.addEventListener('change', listener)
  return () => query.removeEventListener('change', listener)
}

const getOsTheme = (): Theme => (query.matches ? 'dark' : 'light')

const resolveTheme = (preference: ThemePreference, osTheme: Theme): Theme =>
  preference === 'system' ? osTheme : preference

/** 当前实际生效的深浅色：手动选择优先，「跟随系统」时取系统设置 */
export const useTheme = (): Theme => {
  const preference = useSettings((state) => state.themePreference)
  const osTheme = useSyncExternalStore(subscribeOsTheme, getOsTheme)
  return resolveTheme(preference, osTheme)
}

/** 原生 StatusBar 插件只接受 #RRGGBB / #RRGGBBAA，而浏览器计算值是 rgb(r, g, b)。 */
const toNativeColor = (color: string): string => {
  const match = /^rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(color.trim())
  if (!match) {
    return color
  }
  const hex = match.slice(1, 4).map((channel) => Number(channel).toString(16).padStart(2, '0'))
  return '#' + hex.join('')
}

/** APK 内的原生状态栏（overlaysWebView=false）：底色与图标明暗跟随页面主题。 */
const syncNativeStatusBar = (backgroundColor: string, dark: boolean) => {
  // POC 裸 WebView 没有 Capacitor StatusBar 插件，走 FlyAIPoc 桥同步系统栏
  const poc = window as unknown as {
    __flyaiPoc?: { token?: string }
    FlyAIPoc?: { setStatusBar?: (token: string, color: string, dark: string) => string }
  }
  if (poc.__flyaiPoc?.token && poc.FlyAIPoc?.setStatusBar) {
    try {
      poc.FlyAIPoc.setStatusBar(
        poc.__flyaiPoc.token,
        toNativeColor(backgroundColor),
        dark ? '1' : '0'
      )
    } catch {
      // 桥不可用时忽略，不影响主题切换
    }
  }
  const statusBar = (
    window as unknown as {
      Capacitor?: {
        Plugins?: {
          StatusBar?: {
            setBackgroundColor?: (options: { color: string }) => unknown
            setStyle?: (options: { style: string }) => unknown
          }
        }
      }
    }
  ).Capacitor?.Plugins?.StatusBar
  if (!statusBar) {
    return
  }
  // 颜色转换失败或插件调用被拒时要静默忽略，不能影响页面主题切换。
  void Promise.resolve(statusBar.setBackgroundColor?.({ color: toNativeColor(backgroundColor) })).catch(() => {})
  void Promise.resolve(statusBar.setStyle?.({ style: dark ? 'DARK' : 'LIGHT' })).catch(() => {})
}

const syncPageBackground = () => {
  const backgroundColor = getComputedStyle(document.body).backgroundColor
  document.documentElement.style.backgroundColor = backgroundColor
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', backgroundColor)
  syncNativeStatusBar(backgroundColor, document.body.classList.contains('dark'))
}

/** 同步页面主题、根画布背景和支持 theme-color 的浏览器栏。 */
const applyTheme = (theme: Theme) => {
  const body = document.body
  // 主题切换帧禁用过渡，使页面颜色同步更新
  body.classList.add('change-theme')
  body.classList.toggle('dark', theme === 'dark')
  body.classList.toggle('light', theme === 'light')
  if (theme === 'dark') {
    body.setAttribute('data-ds-dark-theme', 'dark')
  } else {
    body.removeAttribute('data-ds-dark-theme')
  }
  document.documentElement.style.colorScheme = theme
  // 首次背景色同步在样式表加载完成后执行，适配 WebKit 的 defer 脚本执行顺序。
  if (document.readyState === 'complete') {
    syncPageBackground()
  }
  setTimeout(() => {
    body.classList.remove('change-theme')
  }, 0)
}

let appliedTheme: Theme | null = null

const syncTheme = () => {
  const theme = resolveTheme(useSettings.getState().themePreference, getOsTheme())
  if (theme !== appliedTheme) {
    appliedTheme = theme
    applyTheme(theme)
  }
}

/** 首屏渲染前同步应用一次，之后随主题设置与系统深浅色变化更新 */
export const setupTheme = () => {
  syncTheme()
  if (document.readyState !== 'complete') {
    window.addEventListener('load', syncPageBackground, { once: true })
  }
  query.addEventListener('change', syncTheme)
  useSettings.subscribe(syncTheme)
}
