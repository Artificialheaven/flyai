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
