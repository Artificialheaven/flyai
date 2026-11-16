import { create } from 'zustand'
import { getRuntimeConfig } from '../services/api/lore.js'
import type { RuntimeConfigView } from '../types/lore.js'

/**
 * 运行期配置只读缓存：房间相关界面（提供商/音色）先按需拉取，
 * P4 设置中心再在此基础上扩展可编辑视图。
 */
type ConfigStore = {
  config: RuntimeConfigView | null
  loading: boolean
  loaded: boolean
  refresh: () => Promise<void>
  ensureLoaded: () => Promise<void>
}

export const useConfigStore = create<ConfigStore>((set, get) => ({
  config: null,
  loading: false,
  loaded: false,

  refresh: async () => {
    if (get().loading) {
      return
    }
    set({ loading: true })
    try {
      const config = await getRuntimeConfig()
      set({ config, loading: false, loaded: true })
    } catch {
      // 配置拉取失败只影响提供商展示，不阻断聊天
      set({ loading: false })
    }
  },

  ensureLoaded: async () => {
    if (get().loaded || get().loading) {
      return
    }
    await get().refresh()
  },
}))

/** 提供商下拉显示名：id 保证唯一，后缀模型便于区分同 id 多模型 */
export const providerLabel = (provider: { id: string; model?: string }): string =>
  provider.model ? `${provider.id} · ${provider.model}` : provider.id