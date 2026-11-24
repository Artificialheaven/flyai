import { create } from 'zustand'
import * as api from './client.js'
import type { ExtensionItem } from './types.js'

type ExtensionStore = {
  items: ExtensionItem[]
  admin: boolean
  featureEnabled: boolean
  loading: boolean
  error: string
  load: () => Promise<void>
  toggle: (name: string, enabled: boolean) => Promise<void>
  remove: (name: string, purge?: boolean) => Promise<void>
  move: (name: string, delta: number) => Promise<void>
  install: (file: File, opts?: { update?: string; onProgress?: (p: number) => void }) => Promise<void>
}

const sortItems = (items: ExtensionItem[]): ExtensionItem[] =>
  [...items].sort((a, b) => a.order - b.order || a.name.localeCompare(b.name))

export const useExtensions = create<ExtensionStore>((set, get) => ({
  items: [],
  admin: false,
  featureEnabled: true,
  loading: false,
  error: '',
  load: async () => {
    set({ loading: true, error: '' })
    try {
      const data = await api.listExtensions()
      set({ items: sortItems(data.extensions), admin: data.admin, featureEnabled: data.enabled })
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) })
    } finally {
      set({ loading: false })
    }
  },
  toggle: async (name, enabled) => {
    await api.setExtensionEnabled(name, enabled)
    set({ items: get().items.map((item) => (item.name === name ? { ...item, enabled } : item)) })
  },
  remove: async (name, purge = false) => {
    await api.uninstallExtension(name, purge)
    set({ items: get().items.filter((item) => item.name !== name) })
  },
  move: async (name, delta) => {
    const items = [...get().items]
    const index = items.findIndex((item) => item.name === name)
    const target = index + delta
    if (index < 0 || target < 0 || target >= items.length) return
    const [moved] = items.splice(index, 1)
    items.splice(target, 0, moved)
    const order = items.map((item) => item.name)
    await api.setExtensionOrder(order)
    set({ items: items.map((item, i) => ({ ...item, order: i * 10 })) })
  },
  install: async (file, opts) => {
    if (opts?.update) {
      await api.updateExtension(opts.update, file, opts.onProgress)
    } else {
      await api.installExtension(file, opts?.onProgress)
    }
    await get().load()
  },
}))
