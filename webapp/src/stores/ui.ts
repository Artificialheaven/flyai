import { create } from 'zustand'

export type ThemePreference = 'light' | 'dark' | 'system'

/** 与旧站共用 localStorage 键 flyai-theme；旧值只有 light/dark，system 为新增项，默认暗色 */
const STORAGE_KEY = 'flyai-theme'
const VALID: ReadonlySet<string> = new Set(['light', 'dark', 'system'])

const readInitial = (): ThemePreference => {
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved && VALID.has(saved) ? (saved as ThemePreference) : 'dark'
}

type UiState = {
  themePreference: ThemePreference
  setThemePreference: (preference: ThemePreference) => void
}

export const useUi = create<UiState>((set) => ({
  themePreference: readInitial(),
  setThemePreference: (themePreference) => {
    localStorage.setItem(STORAGE_KEY, themePreference)
    set({ themePreference })
  },
}))
