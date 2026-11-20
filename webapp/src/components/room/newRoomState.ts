import { create } from 'zustand'

/** 新建房间弹窗：多角色 + 可选世界观，一次建好多 AI 房间。 */
export const useNewRoomDialog = create<{ open: boolean; presetIds: string[] }>(() => ({
  open: false,
  presetIds: [],
}))

/** 打开弹窗；presetIds 用于内容库「用此卡开聊」预选角色。 */
export const openNewRoom = (presetIds: string[] = []) =>
  useNewRoomDialog.setState({ open: true, presetIds })

export const closeNewRoom = () =>
  useNewRoomDialog.setState({ open: false, presetIds: [] })