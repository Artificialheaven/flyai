import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { buildHistoryItems, type HistoryItem } from '../../services/chat/history.js'
import { useChatStore } from '../../stores/chatStore.js'
import type { Message } from '../../types/session.js'
import { ScrollToBottomContext } from '../../components/messages/scrollContext.js'
import { ImageViewer, type ViewerImage } from '../images/ImageViewer.js'
import { AssistantBubble } from './messages/AssistantBubble.js'
import { buildPixivReveal } from './messages/markers.js'
import { UserBubble } from './messages/UserBubble.js'
import { chainToView, liveToView } from './messages/viewModels.js'
import styles from './MessageList.module.css'

const TombstoneRow = ({ message, onRestore }: { message: Message; onRestore: (text: string) => void }) => (
  <div className={styles.tombstone}>
    <span>一条已撤回的消息</span>
    {message.tombstone?.content ? (
      <button type="button" className={styles.tombstoneAction} onClick={() => onRestore(message.tombstone!.content!)}>
        恢复到输入框
      </button>
    ) : null}
  </div>
)

export const MessageList = () => {
  const detail = useChatStore((state) => state.detail)
  const tid = useChatStore((state) => state.tid)
  const live = useChatStore((state) => state.live)
  const lastSent = useChatStore((state) => state.lastSent)
  const setDraft = useChatStore((state) => state.setDraft)

  const thread = detail?.threads.find((item) => item.id === tid)
  const isGroup = thread?.kind === 'group'
  const canEdit = thread?.kind === 'dm'

  const items: HistoryItem[] = useMemo(() => buildHistoryItems(thread?.messages ?? []), [thread?.messages])

  // P 站精选原图回填：按渲染顺序串起历史与流式工具，pick_pixiv 把原图填回其前一段 search_pixiv
  const pixivReveal = useMemo(() => {
    const entries: Array<{ id: string; name: string; content: string | null }> = []
    for (const item of items) {
      if (item.type === 'assistant') {
        for (const tool of item.tools) {
          entries.push({ id: tool.id, name: tool.name, content: tool.content })
        }
      }
    }
    if (live && !(live.threadId && tid && live.threadId !== tid)) {
      for (const card of live.assistants) {
        for (const tool of card.tools) {
          entries.push({ id: tool.id, name: tool.name, content: tool.output ?? tool.preview ?? null })
        }
      }
    }
    return buildPixivReveal(entries)
  }, [items, live, tid])

  const liveCards = useMemo(() => {
    if (!live) {
      return []
    }
    // 新会话首条消息时 tid 仍为 null，线程 id 要等 message_start 帧由服务端下发；
    // 两边都已知时才做线程匹配，避免流式卡片被整段过滤
    if (live.threadId && tid && live.threadId !== tid) {
      return []
    }
    return live.assistants.filter((card) => !card.skipped).map((card) => liveToView(card, pixivReveal))
  }, [live, tid, pixivReveal])

  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const [viewer, setViewer] = useState<ViewerImage | null>(null)

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current
    if (!el) {
      return
    }
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    stickRef.current = true
    setShowJump(false)
  }, [])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) {
      return
    }
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    stickRef.current = nearBottom
    setShowJump(!nearBottom)
  }

  useLayoutEffect(() => {
    if (stickRef.current) {
      const el = scrollRef.current
      if (el) {
        el.scrollTop = el.scrollHeight
      }
    }
  }, [items, liveCards, lastSent])

  let anchor: string | null = null

  return (
    <ScrollToBottomContext.Provider value={() => scrollToBottom()}>
      <div ref={scrollRef} className={styles.list} onScroll={handleScroll}>
        <div className={styles.inner}>
          {items.map((item) => {
            if (item.type === 'tombstone') {
              return (
                <TombstoneRow
                  key={item.message.id}
                  message={item.message}
                  onRestore={(text) => setDraft(text)}
                />
              )
            }
            if (item.type === 'user') {
              anchor = item.message.id
              return (
                <UserBubble
                  key={item.message.id}
                  message={item.message}
                  canEdit={canEdit}
                  onOpenImage={setViewer}
                />
              )
            }
            const view = chainToView(item, pixivReveal)
            return (
              <AssistantBubble
                key={item.key}
                view={view}
                showName={isGroup}
                canRegenerate={canEdit}
                anchorId={anchor}
                onOpenImage={setViewer}
              />
            )
          })}

          {lastSent ? (
            <div className={styles.userRow}>
              <div className={styles.optimisticBubble}>
                <span className={styles.optimisticText}>{lastSent.content}</span>
              </div>
            </div>
          ) : null}

          {liveCards.map((view) => (
            <AssistantBubble
              key={`live-${view.key}`}
              view={view}
              showName={isGroup}
              canRegenerate={false}
              anchorId={null}
              onOpenImage={setViewer}
            />
          ))}
        </div>
      </div>

      {showJump ? (
        <button type="button" className={styles.jumpButton} onClick={() => scrollToBottom(true)}>
          ↓ 回到底部
        </button>
      ) : null}

      {viewer ? <ImageViewer image={viewer} onClose={() => setViewer(null)} /> : null}
    </ScrollToBottomContext.Provider>
  )
}
