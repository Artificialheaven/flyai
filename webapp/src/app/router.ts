import { useSyncExternalStore } from 'react'

/**
 * 极简 hash 路由：后端没有 SPA history fallback、Capacitor 以 https://localhost 加载本地文件，
 * 只有 hash 路由在两种宿主下都零配置可用。
 */
const subscribe = (onChange: () => void) => {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

/** 当前路由路径，去掉开头的 #；空 hash 归一化为 '/' */
const getHashPath = (): string => {
  const raw = window.location.hash.replace(/^#/, '')
  return raw || '/'
}

export const useHashRoute = (): string => useSyncExternalStore(subscribe, getHashPath)

export const navigate = (path: string): void => {
  const target = path.startsWith('#') ? path : `#${path}`
  if (window.location.hash === target) {
    return
  }
  window.location.hash = target
}
