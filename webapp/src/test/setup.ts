/**
 * Vitest 全局准备。
 *
 * 默认环境是 node；需要 DOM 的用例在文件头用
 * `// @vitest-environment happy-dom` 单独声明。
 * 这里只补齐少数模块在导入期会触达的浏览器全局，避免 Node 下报错。
 */

if (typeof globalThis.localStorage === 'undefined') {
  const store = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key)
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
  })
}
