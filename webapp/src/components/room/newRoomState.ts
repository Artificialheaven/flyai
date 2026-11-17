import { create } from 'zustand'

/** 新建房间弹窗：多角色 + 可选世界观，一次建好多 AI 房间。 */
export const useNewRoomDialog = create<{ open: boolean }>(() => ({ open: false }))

export const openNewRoom = () => useNewRoomDialog.setState({ open: true })

export const closeNewRoom = () => useNewRoomDialog.setState({ open: false })