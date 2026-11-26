/** 输入框回车行为：可单测的纯函数，避免键盘逻辑散落在组件里。 */

export type EnterAction = 'send' | 'newline' | 'none'

export type EnterKeyInput = {
  key: string
  ctrlKey?: boolean
  metaKey?: boolean
  shiftKey?: boolean
  /** 中文输入法组合中（拼音未上屏）不处理 */
  isComposing?: boolean
}

/**
 * enterToSend=true：Enter 发送，Ctrl/Cmd+Enter 换行（Shift+Enter 交给浏览器换行）
 * enterToSend=false：Enter 换行，Ctrl/Cmd+Enter 发送（手机用发送按钮）
 */
export const resolveEnterAction = (
  input: EnterKeyInput,
  enterToSend: boolean
): EnterAction => {
  if (input.key !== 'Enter' || input.isComposing) {
    return 'none'
  }
  const modifier = Boolean(input.ctrlKey || input.metaKey)
  if (enterToSend) {
    if (modifier) {
      return 'newline'
    }
    // Shift+Enter 交给 textarea 默认换行，避免重复插入
    return input.shiftKey ? 'none' : 'send'
  }
  return modifier ? 'send' : 'none'
}
