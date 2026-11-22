/**
 * 酒馆助手作者脚本面板容器（P1）。
 *
 * 无同源权限的沙箱 iframe：脚本读写不到宿主登录态；只接收当前会话的
 * 消息文本与变量快照。高度通过 postMessage 自适应。
 */

import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { CardWriteContext, StAuthorPayload } from '../../services/flyai/types.js'
import {
  writeSessionCharacter,
  writeSessionMvu,
  writeSessionWorld,
} from '../../services/flyai/st.js'
import { setDraftText } from '../../services/chat/drafts.js'
import {
  continueReply,
  regenerateReply,
  sendCardMessage,
} from '../../services/chat/store.js'
import {
  AUTHOR_MSG_ACTION,
  AUTHOR_MSG_COMPOSE,
  AUTHOR_MSG_EVENT,
  AUTHOR_MSG_VARS,
  AUTHOR_MSG_WRITE,
  AUTHOR_MSG_WRITE_CHARACTER,
  AUTHOR_MSG_WRITE_WORLD,
  buildAuthorDoc,
  type AuthorRuntimeMessage,
} from '../../services/st/authorRuntime.js'
import { CARD_APP_PROTOCOL_VERSION, CARD_MSG_RESIZE } from '../../services/st/cardApp.js'
import {
  CardActionDialog,
  type CardConfirmRequest,
} from '../messages/CardActionDialog.js'

const MIN_HEIGHT = 160
const MAX_HEIGHT = 2400
const INITIAL_HEIGHT = 480

const clampHeight = (value: number): number =>
  Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(value)))

export const AuthorPanelFrame = memo(function AuthorPanelFrame({
  html,
  author,
  messages,
  variables,
  character,
  externalMedia = false,
  sessionId,
  threadId,
  canWrite = false,
  canAct = false,
  actionMessageId,
  writeContext,
  streaming = false,
}: {
  html: string
  author: StAuthorPayload
  messages: AuthorRuntimeMessage[]
  variables?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  externalMedia?: boolean
  /** 卡内指令填入真实输入框需要会话 id */
  sessionId?: string
  /** 私聊窗口 id；写入变量时回传 */
  threadId?: string
  /** 是否允许把面板写入落到会话副本（st_card_write） */
  canWrite?: boolean
  /** 是否允许发送/继续/重生成（st_card_actions；逐次确认） */
  canAct?: boolean
  /** 继续/重生成的目标消息 id（通常是最后一条助手回复） */
  actionMessageId?: string
  /** 世界书/角色副本读取快照 */
  writeContext?: CardWriteContext
  /** 当前会话是否正在生成；用于向卡内派发 GENERATION_* 事件 */
  streaming?: boolean
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [height, setHeight] = useState(INITIAL_HEIGHT)
  const [confirmRequest, setConfirmRequest] = useState<CardConfirmRequest | null>(null)
  const confirmResolve = useRef<((ok: boolean) => void) | null>(null)
  // 消息/变量变化不重建 iframe：作者脚本的标签页、主题与滚动位置要保留。
  const initialMessages = useRef(messages)
  const pendingWrite = useRef<{
    timer: ReturnType<typeof setTimeout>
    stat: Record<string, unknown>
  } | null>(null)

  const doc = useMemo(
    () =>
      buildAuthorDoc({
        html,
        author,
        messages: initialMessages.current,
        variables,
        character,
        externalMedia,
        writeContext,
      }),
    // 消息文本/变量只做初始快照；后续变化走 postMessage，避免重载 iframe。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [html, author, character?.name, character?.avatar, externalMedia, writeContext]
  )

  const latestRef = useRef({ variables, messages })
  latestRef.current = { variables, messages }

  const askConfirm = (request: CardConfirmRequest): Promise<boolean> =>
    new Promise((resolve) => {
      confirmResolve.current = resolve
      setConfirmRequest(request)
    })

  const finishConfirm = (ok: boolean) => {
    const resolve = confirmResolve.current
    confirmResolve.current = null
    setConfirmRequest(null)
    resolve?.(ok)
  }

  const flushWrite = () => {
    const pending = pendingWrite.current
    if (!pending || !sessionId || !canWrite) {
      return
    }
    pendingWrite.current = null
    clearTimeout(pending.timer)
    void writeSessionMvu(sessionId, {
      mode: 'replace',
      stat_data: pending.stat,
      thread_id: threadId,
    }).catch(() => {
      // 写入失败保留面板本地状态；用户继续操作会再次提交
    })
  }

  const queueWrite = (stat: Record<string, unknown>) => {
    if (!sessionId || !canWrite) {
      return
    }
    const pending = pendingWrite.current
    if (pending) {
      pending.stat = stat
      return
    }
    pendingWrite.current = {
      stat,
      timer: setTimeout(flushWrite, 350),
    }
  }

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return
      }
      const data = event.data as {
        type?: unknown
        height?: unknown
        text?: unknown
        stat_data?: unknown
      } | null
      if (!data) {
        return
      }
      if (data.type === CARD_MSG_RESIZE) {
        const next = Number(data.height)
        if (Number.isFinite(next)) {
          setHeight(clampHeight(next))
        }
        return
      }
      if (data.type === AUTHOR_MSG_COMPOSE && sessionId) {
        setDraftText(sessionId, String(data.text ?? ''))
        return
      }
      if (data.type === AUTHOR_MSG_WRITE && data.stat_data && typeof data.stat_data === 'object') {
        queueWrite(data.stat_data as Record<string, unknown>)
        return
      }
      if (data.type === AUTHOR_MSG_WRITE_WORLD && canWrite && sessionId && Array.isArray((data as { entries?: unknown }).entries)) {
        void writeSessionWorld(
          sessionId,
          (data as { entries: unknown[] }).entries
        ).catch(() => {})
        return
      }
      if (data.type === AUTHOR_MSG_WRITE_CHARACTER && canWrite && sessionId) {
        const fields = (data as { fields?: unknown }).fields
        if (fields && typeof fields === 'object') {
          void writeSessionCharacter(
            sessionId,
            fields as Record<string, unknown>
          ).catch(() => {})
        }
        return
      }
      if (data.type === AUTHOR_MSG_ACTION && canAct && sessionId) {
        const action = String((data as { action?: unknown }).action ?? '')
        if (action !== 'send' && action !== 'continue' && action !== 'regenerate') {
          return
        }
        const text = String((data as { text?: unknown }).text ?? '')
        void (async () => {
          const allowed = await askConfirm({
            kind: 'intent',
            intent: { kind: 'action', action, text },
          })
          if (!allowed) {
            return
          }
          if (action === 'send') {
            sendCardMessage(sessionId, text)
          } else if (action === 'continue') {
            if (actionMessageId) {
              continueReply(sessionId, actionMessageId)
            }
          } else {
            regenerateReply(sessionId, actionMessageId)
          }
        })()
      }
    }
    window.addEventListener('message', receive)
    return () => {
      window.removeEventListener('message', receive)
      if (pendingWrite.current) {
        flushWrite()
      }
    }
    // flushWrite/queueWrite 只依赖这些稳定参数
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, threadId, canWrite, canAct, actionMessageId])

  const pushContext = () => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        {
          v: CARD_APP_PROTOCOL_VERSION,
          type: AUTHOR_MSG_VARS,
          variables: latestRef.current.variables ?? {},
          messages: latestRef.current.messages,
        },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  useEffect(() => {
    pushContext()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variables, messages])

  useEffect(() => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        {
          v: CARD_APP_PROTOCOL_VERSION,
          type: AUTHOR_MSG_EVENT,
          event: streaming ? 'GENERATION_STARTED' : 'GENERATION_ENDED',
        },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }, [streaming])

  if (!doc) {
    return null
  }

  return (
    <>
    <iframe
      ref={iframeRef}
      title="酒馆助手脚本面板"
      sandbox="allow-scripts allow-modals"
      srcDoc={doc}
      onLoad={pushContext}
      style={{
        width: '100%',
        height,
        border: 'none',
        display: 'block',
        background: 'transparent',
      }}
    />
    {confirmRequest ? (
      <CardActionDialog
        request={confirmRequest}
        onConfirm={() => finishConfirm(true)}
        onCancel={() => finishConfirm(false)}
      />
    ) : null}
    </>
  )
})
