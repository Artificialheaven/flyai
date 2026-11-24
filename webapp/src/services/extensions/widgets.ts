import { create } from 'zustand'

export type ExtensionWidget = {
  ext: string
  slot: 'message' | 'header'
  id: string
  label: string
  title: string
  command: string
}

type WidgetState = {
  widgets: ExtensionWidget[]
  register: (widget: ExtensionWidget) => void
  clearExt: (ext: string) => void
  clearAll: () => void
}

/** L2 宿主挂件位：扩展通过 ui.widget.register 注册，React 侧原生渲染。 */
export const useExtensionWidgets = create<WidgetState>((set) => ({
  widgets: [],
  register: (widget) =>
    set((state) => {
      const next = state.widgets.filter(
        (item) => !(item.ext === widget.ext && item.slot === widget.slot && item.id === widget.id)
      )
      return { widgets: [...next, widget] }
    }),
  clearExt: (ext) => set((state) => ({ widgets: state.widgets.filter((item) => item.ext !== ext) })),
  clearAll: () => set({ widgets: [] }),
}))

type ErrorState = {
  errors: Record<string, string[]>
  add: (ext: string, message: string) => void
  clear: (ext: string) => void
  clearAll: () => void
}

/** 运行期错误（最近 50 条/扩展），管理面板展示，便于定位缺失模块 */
export const useExtensionErrors = create<ErrorState>((set) => ({
  errors: {},
  add: (ext, message) =>
    set((state) => {
      const list = [...(state.errors[ext] || []), message].slice(-50)
      return { errors: { ...state.errors, [ext]: list } }
    }),
  clear: (ext) =>
    set((state) => {
      const next = { ...state.errors }
      delete next[ext]
      return { errors: next }
    }),
  clearAll: () => set({ errors: {} }),
}))
