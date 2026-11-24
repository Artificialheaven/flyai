import { useEffect } from 'react'
import { useChatStore } from '../../services/chat/store.js'
import { extensionHost } from '../../services/extensions/hostManager.js'

/** 无渲染的扩展宿主挂载点：会话切换时重建 L1 扩展 iframe。 */
export const ExtensionHost = () => {
  const activeId = useChatStore((state) => state.activeId || '')
  useEffect(() => {
    if (activeId) {
      void extensionHost.sync(activeId)
    } else {
      extensionHost.destroy()
    }
    return () => {
      extensionHost.destroy()
    }
  }, [activeId])
  return null
}
