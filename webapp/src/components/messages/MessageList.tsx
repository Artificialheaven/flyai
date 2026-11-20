import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useIsMobile } from '../../app/viewport.js'
import {
  buildMessageVersions,
  getVisibleMessages,
  type MessageVersions,
} from '../../services/chat/branches.js'
import { selectMessageVersion } from '../../services/chat/store.js'
import type { Session } from '../../services/chat/types.js'
import { closeSearchPanel } from '../search/searchPanelState.js'
import type { MarkdownRenderOptions } from '../markdown/SanitizedHtml.js'
import type { CardAppCapabilities, CardAppWriteContext } from '../../services/st/cardApp.js'
import { CardAppCharacterContext } from '../markdown/CardAppFrame.js'
import { AssistantMessage } from './AssistantMessage.js'
import { ScrollToBottomContext } from './scrollContext.js'
import { TombstoneNotice } from './TombstoneNotice.js'
import { UserMessage } from './UserMessage.js'
import type { Message } from '../../services/chat/types.js'

/**
 * 会话共用可重试的附件上传失败入口，覆盖当前提问与历史消息中的附件。
 * 点击后按失败的回答重新补传附件并请求。
 */
const getUploadRetryId = (messages: Message[], streaming: boolean) => {
  const last = messages.at(-1)
  return !streaming &&
    last?.role === 'assistant' &&
    last.status === 'error' &&
    last.error?.kind === 'upload' &&
    last.error.retryable
    ? last.id
    : null
}
import styles from './MessageList.module.css'

/** 距底部小于该值时视为在底部，流式内容增长时跟随 */
const STICKY_THRESHOLD = 40
const EMPTY_VERSIONS: MessageVersions = { ids: [], index: -1 }

export const MessageList = ({
  session,
  streaming,
  footer,
}: {
  session: Session
  streaming: boolean
  footer: React.ReactNode
}) => {
  const isMobile = useIsMobile()
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const stickyRef = useRef(true)
  const scrollTopRef = useRef(0)
  const restoreScrollRef = useRef<{ messageId: string; top: number } | null>(null)
  const messages = getVisibleMessages(session)
  const [previousVersions, setPreviousVersions] = useState(() =>
    buildMessageVersions(session, new Map())
  )
  const versions = useMemo(
    () => buildMessageVersions(session, previousVersions),
    [session, previousVersions]
  )
  if (versions !== previousVersions) {
    setPreviousVersions(versions)
  }
  const lastMessageId = messages.at(-1)?.id
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el && stickyRef.current) {
      el.scrollTop = el.scrollHeight
      scrollTopRef.current = el.scrollTop
    }
  }, [])

  // 内容、键盘和输入框高度变化时，继续遵循当前跟随状态。
  useLayoutEffect(() => {
    const content = contentRef.current
    const scroll = scrollRef.current
    if (!content || !scroll) {
      return
    }
    const observer = new ResizeObserver(scrollToBottom)
    observer.observe(content)
    observer.observe(scroll)
    return () => observer.disconnect()
  }, [isMobile, scrollToBottom])

  // 切换版本后将选择器留在原位置，避免回答长短变化导致操作区跳出视口。
  useLayoutEffect(() => {
    const anchor = restoreScrollRef.current
    const scroll = scrollRef.current
    if (anchor && scroll) {
      const switcher = scroll.querySelector<HTMLElement>(
        `[data-message-version="${CSS.escape(anchor.messageId)}"]`
      )
      if (switcher) {
        scroll.scrollTop += switcher.getBoundingClientRect().top - anchor.top
      }
      restoreScrollRef.current = null
      return
    }
    stickyRef.current = true
    scrollToBottom()
  }, [session.id, lastMessageId, scrollToBottom])

  useEffect(closeSearchPanel, [session.id, lastMessageId])

  const selectVersion = useCallback(
    (messageId: string, anchorTop: number) => {
      if (streaming) {
        return
      }
      restoreScrollRef.current = { messageId, top: anchorTop }
      stickyRef.current = false
      closeSearchPanel()
      selectMessageVersion(session.id, messageId)
    },
    [session.id, streaming]
  )

  // 跨移动端断点时切换滚动层，并保持输入框及当前阅读位置。
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) {
      return
    }
    if (stickyRef.current) {
      scrollToBottom()
    } else {
      el.scrollTop = scrollTopRef.current
    }
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD
      // 仅在用户向上滚动时停止跟随底部，布局或内容增高时保持跟随状态。
      if (nearBottom) {
        stickyRef.current = true
      } else if (el.scrollTop < scrollTopRef.current) {
        stickyRef.current = false
      }
      scrollTopRef.current = el.scrollTop
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [isMobile, scrollToBottom])

  const lastIndex = messages.length - 1
  // 酒馆展示侧正则：按消息所属角色卡取脚本，缺省用会话当前角色的卡。
  // 会话缓存里的 st 对象保持稳定，因此脚本数组的引用也不变，Markdown 的 memo 有效。
  const stScriptsFor = useMemo(() => {
    const st = session.st
    if (!st || st.flags?.regex_display === false) {
      return (_characterId?: string) => undefined
    }
    const fallback = st.regex_scripts ?? []
    const byCharacter = st.by_character ?? {}
    return (characterId?: string) => {
      const own = characterId ? byCharacter[characterId] : undefined
      const scripts = own && own.length > 0 ? own : fallback
      return scripts.length > 0 ? scripts : undefined
    }
  }, [session.st])
  // 酒馆卡 MVU：每条消息优先用自己的变量快照，旧消息回退到会话当前状态。
  // 只用于展开展示侧变量宏，不影响存储原文与复制/朗读。
  const stVariablesFor = useMemo(() => {
    const mvu = session.st?.mvu
    if (!mvu?.enabled) {
      return (_message: { statData?: Record<string, unknown> }) => undefined
    }
    const current = mvu.stat_data
    return (message: { statData?: Record<string, unknown> }) =>
      message.statData ?? current
  }, [session.st])
  // 酒馆卡 HTML：后端已按用户开关算好；关闭时保持旧的转义文本行为。
  const stHtml = useMemo<MarkdownRenderOptions | undefined>(() => {
    const st = session.st
    if (!st || st.flags?.html === false || !st.html?.enabled) {
      return undefined
    }
    return {
      htmlEnabled: true,
      externalImages: !!st.html.external_images,
      imageProxy: !!st.html.image_proxy && !!st.flags?.image_proxy,
      jsSandbox: st.flags?.js_sandbox === true,
    }
  }, [session.st])
  // 卡内脚本可读取当前角色名/头像；头像源随 doc 进入 CSP img-src。
  const cardCharacter = useMemo(() => {
    const name = session.characterName
    const avatar = session.characterAvatar
    return name || avatar ? { name, avatar } : undefined
  }, [session.characterName, session.characterAvatar])
  // 卡片能力由后端按管理员配置 + 用户开关 + 权限等级算好；缺省全部关闭。
  // 会话缓存里的 st 对象保持稳定，因此能力的引用也不变，消息组件 memo 有效。
  const cardFeatures = useMemo<CardAppCapabilities>(
    () => ({
      actions: session.st?.flags?.card_actions === true,
      write: session.st?.flags?.card_write === true,
      net: session.st?.flags?.net_proxy === true,
    }),
    [session.st]
  )
  const cardWriteContext = (session.st?.write_context ?? null) as CardAppWriteContext | null
  const uploadRetryId = getUploadRetryId(messages, streaming)
  // 群聊窗口由后端按座位分别应答，暂不支持编辑/重新生成；只读会话同理。
  const editable = !session.readOnly && session.threadKind !== 'group'
  return (
    <ScrollToBottomContext.Provider value={scrollToBottom}>
      <div ref={isMobile ? undefined : scrollRef} className={styles.container}>
        <div ref={isMobile ? scrollRef : undefined} className={styles.scroll}>
          <CardAppCharacterContext.Provider value={cardCharacter}>
            <div ref={contentRef} className={styles.column}>
              {messages.map((message, index) =>
                message.role === 'tombstone' ? (
                  <TombstoneNotice
                    key={message.id}
                    sessionId={session.id}
                    message={message}
                    isFirst={index === 0}
                    busy={streaming}
                  />
                ) : message.role === 'user' ? (
                  <UserMessage
                    key={message.id}
                    sessionId={session.id}
                    message={message}
                    retryUploadId={uploadRetryId}
                    isFirst={index === 0}
                    isLast={index === lastIndex || index === lastIndex - 1}
                    busy={streaming}
                    editable={editable}
                    versions={versions.get(message.id) ?? EMPTY_VERSIONS}
                    onSelectVersion={selectVersion}
                  />
                ) : (
                  <AssistantMessage
                    key={message.id}
                    sessionId={session.id}
                    message={message}
                    isLast={index === lastIndex}
                    streaming={streaming && index === lastIndex}
                    busy={streaming}
                    showSpeaker={session.threadKind === 'group'}
                    editable={editable}
                    versions={versions.get(message.id) ?? EMPTY_VERSIONS}
                    onSelectVersion={selectVersion}
                    stScripts={stScriptsFor(message.characterId ?? session.characterId)}
                    stDepth={lastIndex - index}
                    stHtml={stHtml}
                    stVariables={stVariablesFor(message)}
                    stPanels={session.st?.panels}
                    cardFeatures={cardFeatures}
                    cardWriteContext={cardWriteContext}
                    threadId={session.threadId}
                  />
                )
              )}
            </div>
          </CardAppCharacterContext.Provider>
        </div>
        {footer}
      </div>
    </ScrollToBottomContext.Provider>
  )
}
