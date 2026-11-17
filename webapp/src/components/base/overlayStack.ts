/**
 * 叠层关闭栈：Android 返回键优先关闭最上层的弹窗/浮层。
 * Modal / ImageViewer / Menu / Select 打开时压栈，卸载或关闭时出栈；
 * 栈底先打开，栈顶最后打开（后开先关）。
 */
import { useEffect, useRef } from 'react'

type Closer = () => void

const stack: Closer[] = []

/** 压入一个关闭动作，返回出栈函数（在 effect cleanup 里调用） */
export const pushOverlay = (close: Closer): (() => void) => {
  stack.push(close)
  return () => {
    const index = stack.indexOf(close)
    if (index >= 0) {
      stack.splice(index, 1)
    }
  }
}

/** 关闭最上层；没有打开的浮层时返回 false（返回键继续向下传递） */
export const closeTopOverlay = (): boolean => {
  const close = stack[stack.length - 1]
  if (!close) {
    return false
  }
  close()
  return true
}

/** 指定关闭动作是否是最上层（Esc 只关最上层浮层） */
export const isTopOverlay = (close: Closer): boolean => stack[stack.length - 1] === close

/** 叠层打开期间把关闭动作挂到返回键栈上；close 用 ref 转发，避免每次渲染重挂 */
export const useOverlayClose = (open: boolean, close: Closer) => {
  const closeRef = useRef(close)
  closeRef.current = close
  useEffect(() => {
    if (!open) {
      return undefined
    }
    return pushOverlay(() => closeRef.current())
  }, [open])
}