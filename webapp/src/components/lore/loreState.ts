import { create } from 'zustand'

/** 内容库弹窗：角色 / 世界观两个标签页。 */
export type LoreTab = 'characters' | 'worlds'

export const useLoreDialog = create<{ open: boolean; tab: LoreTab }>(() => ({
  open: false,
  tab: 'characters',
}))

export const openLore = (tab: LoreTab = 'characters') =>
  useLoreDialog.setState({ open: true, tab })

export const closeLore = () => useLoreDialog.setState({ open: false })