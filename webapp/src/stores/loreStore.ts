import { create } from 'zustand'
import * as api from '../services/api/lore.js'
import { showToast } from '../components/base/toastState.js'
import { useAuth } from './auth.js'
import type { Character, CharacterPayload, World, WorldPayload } from '../types/lore.js'

/** 内容库选择器（对照旧版 rootCards/variantCards/isOwnCard/cardDisplayName） */
export const isOwnCard = (card: Character | null | undefined, uid: string | null, isAdmin: boolean): boolean => {
  if (!card) {
    return false
  }
  if (card.author === uid) {
    return true
  }
  // 历史管理员卡 author 为空
  return isAdmin && !card.author
}

export const rootCards = (characters: Character[]): Character[] =>
  characters.filter((card) => !card.parent_id)

export const variantCards = (characters: Character[], rootId: string): Character[] =>
  characters.filter((card) => card.parent_id === rootId)

export const cardDisplayName = (card: Character | null | undefined): string =>
  (card && (card.alias || card.name)) || '未命名'

export const characterOf = (characters: Character[], id: string | null | undefined): Character | null =>
  (id && characters.find((card) => card.id === id)) || null

export const worldOf = (worlds: World[], id: string | null | undefined): World | null =>
  (id && worlds.find((world) => world.id === id)) || null

type LoreStore = {
  characters: Character[]
  worlds: World[]
  loading: boolean
  loaded: boolean
  refresh: () => Promise<void>
  ensureLoaded: () => Promise<void>
  saveCharacter: (payload: CharacterPayload, id?: string) => Promise<Character>
  removeCharacter: (id: string) => Promise<void>
  forkCharacter: (id: string) => Promise<Character>
  publishCharacter: (id: string) => Promise<void>
  unpublishCharacter: (id: string) => Promise<void>
  saveWorld: (payload: WorldPayload, id?: string) => Promise<World>
  removeWorld: (id: string) => Promise<void>
}

export const useLoreStore = create<LoreStore>((set, get) => ({
  characters: [],
  worlds: [],
  loading: false,
  loaded: false,

  refresh: async () => {
    set({ loading: true })
    try {
      const [characters, worlds] = await Promise.all([api.listCharacters(), api.listWorlds()])
      set({ characters, worlds, loading: false, loaded: true })
    } catch (error) {
      set({ loading: false })
      showToast(`内容库加载失败：${(error as Error).message}`, true)
    }
  },

  ensureLoaded: async () => {
    if (get().loaded || get().loading) {
      return
    }
    await get().refresh()
  },

  saveCharacter: async (payload, id) => {
    const saved = id ? await api.updateCharacter(id, payload) : await api.createCharacter(payload)
    await get().refresh()
    return saved
  },

  removeCharacter: async (id) => {
    await api.deleteCharacter(id)
    await get().refresh()
  },

  forkCharacter: async (id) => {
    const created = await api.forkCharacter(id)
    await get().refresh()
    return created
  },

  publishCharacter: async (id) => {
    await api.publishCharacter(id)
    await get().refresh()
  },

  unpublishCharacter: async (id) => {
    await api.unpublishCharacter(id)
    await get().refresh()
  },

  saveWorld: async (payload, id) => {
    const saved = id ? await api.updateWorld(id, payload) : await api.createWorld(payload)
    await get().refresh()
    return saved
  },

  removeWorld: async (id) => {
    await api.deleteWorld(id)
    await get().refresh()
  },
}))

/** 当前登录人下的归属判断（组件里高频用） */
export const useOwnCardCheck = (): ((card: Character | null | undefined) => boolean) => {
  const user = useAuth((state) => state.user)
  return (card) => isOwnCard(card, user?.id ?? null, user?.role === 'admin')
}