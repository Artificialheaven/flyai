import { isNativeApp } from './native.js'

/**
 * 设备类型判定：用于「电脑背景 / 手机背景」二选一。
 *
 * 刻意不用 useIsMobile()——那是 768px 窗口断点，桌面窄窗口会被误判成手机，
 * 而背景配置需要跟随「实际设备」在换设备登录后保持稳定。
 */
export type DeviceKind = 'desktop' | 'mobile'

export const detectDeviceKind = (): DeviceKind => {
  if (isNativeApp()) {
    return 'mobile'
  }
  if (typeof navigator === 'undefined') {
    return 'desktop'
  }
  const ua = navigator.userAgent
  if (/Android|iPhone|iPod|Windows Phone|Mobile/i.test(ua)) {
    return 'mobile'
  }
  // iPadOS 13+ 默认发桌面 UA（Macintosh）：用多点触控与桌面 Mac 区分
  if (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) {
    return 'mobile'
  }
  return 'desktop'
}

/** 进程内只算一次：设备类型不会在会话中途改变 */
export const deviceKind = detectDeviceKind()
