import { vi } from 'vitest'

/** node 环境没有 localStorage；用内存 Map 实现 Storage 接口供 store/标记模块使用 */
const createMemStorage = (): Storage => {
  const map = new Map<string, string>()
  return {
    getItem: (key) => (map.has(key) ? (map.get(key) as string) : null),
    setItem: (key, value) => {
      map.set(key, String(value))
    },
    removeItem: (key) => {
      map.delete(key)
    },
    clear: () => map.clear(),
    key: (index) => Array.from(map.keys())[index] ?? null,
    get length() {
      return map.size
    },
  }
}

vi.stubGlobal('localStorage', createMemStorage())
