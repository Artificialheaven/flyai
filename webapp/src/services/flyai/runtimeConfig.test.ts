import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const store = new Map<string, string>()

const installStorage = (): void => {
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => (store.has(key) ? store.get(key) ?? null : null),
    setItem: (key: string, value: string) => store.set(key, String(value)),
    removeItem: (key: string) => store.delete(key),
  })
}

const stubConfig = (config: Record<string, unknown>): void => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: async () => config,
    }))
  )
}

beforeEach(() => {
  vi.resetModules()
  store.clear()
  installStorage()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('运行期服务器配置', () => {
  it('release 构建忽略并清理 localStorage 覆盖', async () => {
    stubConfig({ apiBase: 'https://release.example/', debug: false })
    store.set('flyai-runtime-config', JSON.stringify({ apiBase: 'https://evil.example' }))
    const mod = await import('./runtimeConfig.js')
    await mod.loadRuntimeConfig()
    expect(mod.getApiBase()).toBe('https://release.example')
    expect(mod.isDebugRuntime()).toBe(false)
    expect(store.has('flyai-runtime-config')).toBe(false)
  })

  it('debug 构建允许覆盖服务器地址', async () => {
    stubConfig({ apiBase: 'https://debug.example/', debug: true })
    store.set('flyai-runtime-config', JSON.stringify({ apiBase: 'https://local.example/' }))
    const mod = await import('./runtimeConfig.js')
    await mod.loadRuntimeConfig()
    expect(mod.getApiBase()).toBe('https://local.example')
    expect(mod.isDebugRuntime()).toBe(true)
  })

  it('覆盖项不能把 release 改成 debug', async () => {
    stubConfig({ apiBase: 'https://release.example/', debug: false })
    store.set(
      'flyai-runtime-config',
      JSON.stringify({ apiBase: 'https://local.example', debug: true })
    )
    const mod = await import('./runtimeConfig.js')
    await mod.loadRuntimeConfig()
    expect(mod.isDebugRuntime()).toBe(false)
    expect(mod.getApiBase()).toBe('https://release.example')
  })
})
