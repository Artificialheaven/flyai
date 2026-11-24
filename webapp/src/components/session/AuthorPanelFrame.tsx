/**
 * 酒馆助手作者脚本面板容器（P1）。
 *
 * 无同源权限的沙箱 iframe：脚本读写不到宿主登录态；只接收当前会话的
 * 消息文本与变量快照。高度通过 postMessage 自适应。
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import type { CardWriteContext, StAuthorPayload } from '../../services/flyai/types.js'
import {
  writeSessionCharacter,
  writeSessionMvu,
  writeSessionWorld,
} from '../../services/flyai/st.js'
import { setDraftText } from '../../services/chat/drafts.js'
import {
  continueReply,
  generateCardReply,
  regenerateReply,
  sendCardMessage,
} from '../../services/chat/store.js'
import {
  AUTHOR_MSG_ACTION,
  AUTHOR_MSG_COMPOSE,
  AUTHOR_MSG_EVENT,
  AUTHOR_MSG_GENERATE,
  AUTHOR_MSG_GENERATE_MAIN,
  AUTHOR_MSG_GENERATE_MAIN_RESULT,
  AUTHOR_MSG_GLOBAL_VARS,
  AUTHOR_MSG_GENERATE_RESULT,
  AUTHOR_MSG_INJECT_PROMPTS,
  AUTHOR_MSG_PHONE_MAINLINE,
  AUTHOR_MSG_SAVE_MESSAGES,
  AUTHOR_MSG_SAVE_MESSAGES_RESULT,
  AUTHOR_MSG_SAVE_METADATA,
  AUTHOR_MSG_SAVE_METADATA_RESULT,
  AUTHOR_MSG_VARS,
  AUTHOR_MSG_WRITE,
  AUTHOR_MSG_WRITE_CHARACTER,
  AUTHOR_MSG_WRITE_RESULT,
  AUTHOR_MSG_WRITE_WORLD,
  buildAuthorDoc,
  type AuthorRuntimeMessage,
} from '../../services/st/authorRuntime.js'
import { CARD_APP_PROTOCOL_VERSION, CARD_MSG_RESIZE } from '../../services/st/cardApp.js'
import { request } from '../../services/flyai/client.js'
import {
  CardActionDialog,
  type CardConfirmRequest,
} from '../messages/CardActionDialog.js'
import {
  beginCardActivity,
  endCardActivity,
} from '../../services/st/cardActivity.js'

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
  chatMetadata,
  character,
  externalMedia = false,
  sessionId,
  threadId,
  userName,
  canWrite = false,
  canAct = false,
  actionMessageId,
  writeContext,
  globalVariables,
  streaming = false,
}: {
  html: string
  author: StAuthorPayload
  messages: AuthorRuntimeMessage[]
  variables?: Record<string, unknown> | null
  /** 会话级 chatMetadata（作者手札/地图存档） */
  chatMetadata?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  externalMedia?: boolean
  /** 卡内指令填入真实输入框需要会话 id */
  sessionId?: string
  /** 玩家名（context.name1 / 手机署名） */
  userName?: string
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
  /** 用户级全局变量（跨会话） */
  globalVariables?: Record<string, unknown> | null
  /** 当前会话是否正在生成；用于向卡内派发 GENERATION_* 事件 */
  streaming?: boolean
}) {
  const t = useTranslate()
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [height, setHeight] = useState(INITIAL_HEIGHT)
  const [confirmRequest, setConfirmRequest] = useState<CardConfirmRequest | null>(null)
  // 卡内写入世界书/变量/角色字段：头部下沿的全局进度条
  const beginBusy = useCallback(
    () => beginCardActivity(t('cardSyncProgress')),
    [t]
  )
  const endBusy = endCardActivity
  const confirmResolve = useRef<((ok: boolean) => void) | null>(null)
  // 消息/变量变化不重建 iframe：作者脚本的标签页、主题与滚动位置要保留。
  const initialMessages = useRef(messages)
  const pendingWrite = useRef<{
    timer: ReturnType<typeof setTimeout>
    entries: Array<{ id: string; stat: Record<string, unknown> }>
  } | null>(null)

  const doc = useMemo(
    () =>
      buildAuthorDoc({
        html,
        author,
        messages: initialMessages.current,
        variables,
        chatMetadata,
        character,
        externalMedia,
        writeContext,
        globalVariables,
        sessionId,
        chatId: sessionId,
        userName,
        canWrite,
        canAct,
      }),
    // 消息文本/变量只做初始快照；后续变化走 postMessage，避免重载 iframe。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      html,
      author,
      character?.name,
      character?.avatar,
      externalMedia,
      writeContext,
      sessionId,
      userName,
      canWrite,
      canAct,
    ]
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

  const postGenerateResult = (id: string, payload: Record<string, unknown>) => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        { v: CARD_APP_PROTOCOL_VERSION, type: AUTHOR_MSG_GENERATE_RESULT, id, ...payload },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  const postMetadataResult = (id: string, payload: Record<string, unknown>) => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        { v: CARD_APP_PROTOCOL_VERSION, type: AUTHOR_MSG_SAVE_METADATA_RESULT, id, ...payload },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  const postMessagesResult = (id: string, payload: Record<string, unknown>) => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        { v: CARD_APP_PROTOCOL_VERSION, type: AUTHOR_MSG_SAVE_MESSAGES_RESULT, id, ...payload },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  const postGenerateMainResult = (id: string, payload: Record<string, unknown>) => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        {
          v: CARD_APP_PROTOCOL_VERSION,
          type: AUTHOR_MSG_GENERATE_MAIN_RESULT,
          id,
          ...payload,
        },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  const runGenerate = async (data: {
    id?: unknown
    purpose?: unknown
    messages?: unknown
    systemPrompt?: unknown
    maxTokens?: unknown
  }) => {
    const id = typeof data.id === 'string' ? data.id : ''
    if (!id || !sessionId) {
      return
    }
    const messages = Array.isArray(data.messages)
      ? data.messages.filter(
          (item): item is { role: string; content: string } =>
            !!item &&
            typeof item === 'object' &&
            typeof (item as { role?: unknown }).role === 'string' &&
            typeof (item as { content?: unknown }).content === 'string'
        )
      : []
    const useMessages = messages.slice(0, 40).map((item) => ({
      role: item.role,
      content: item.content.slice(0, 16000),
    }))
    const maxTokens = Math.max(0, Math.min(Number(data.maxTokens) || 0, 8192))
    const preview = useMessages
      .map((item) => `${item.role}: ${item.content}`)
      .join('\n')
      .slice(0, 2000)
    const allowed = await askConfirm({
      kind: 'generate',
      purpose: String(data.purpose ?? 'phone'),
      messageCount: useMessages.length,
      chars: useMessages.reduce((sum, item) => sum + item.content.length, 0),
      maxTokens,
      preview,
    })
    if (!allowed) {
      postGenerateResult(id, { ok: false, error: 'cancelled' })
      return
    }
    try {
      const result = await request<{ text?: string }>(
        `/api/sessions/${encodeURIComponent(sessionId)}/author/generate`,
        {
          method: 'POST',
          body: JSON.stringify({
            purpose: String(data.purpose ?? 'phone'),
            messages: useMessages,
            system_prompt: String(data.systemPrompt ?? '').slice(0, 16000),
            max_tokens: maxTokens,
          }),
        }
      )
      postGenerateResult(id, { ok: true, text: String(result.text ?? '') })
    } catch (error) {
      postGenerateResult(id, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }

  const postWriteResult = (id: string, payload: Record<string, unknown>) => {
    if (!id) {
      return
    }
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage(
        { v: CARD_APP_PROTOCOL_VERSION, type: AUTHOR_MSG_WRITE_RESULT, id, ...payload },
        '*'
      )
    } catch {
      // iframe 已卸载
    }
  }

  const flushWrite = () => {
    const pending = pendingWrite.current
    pendingWrite.current = null
    if (!pending) {
      return
    }
    clearTimeout(pending.timer)
    const stat = pending.entries[pending.entries.length - 1]?.stat
    const failAll = (error: string) => {
      for (const entry of pending.entries) {
        postWriteResult(entry.id, { ok: false, error })
      }
    }
    if (!sessionId || !canWrite || !stat) {
      failAll('read-only')
      return
    }
    beginBusy()
    void writeSessionMvu(sessionId, {
      mode: 'replace',
      stat_data: stat,
      thread_id: threadId,
    })
      .then(() => {
        // 手机保存会 await 这个确认，再把 /api/chats/get 的结果当作落盘依据；
        // 只有服务端真正写回后才回报成功。
        for (const entry of pending.entries) {
          postWriteResult(entry.id, { ok: true })
        }
      })
      .catch((error) => {
        failAll(error instanceof Error ? error.message : String(error))
      })
      .finally(endBusy)
  }

  const queueWrite = (id: string, stat: Record<string, unknown>) => {
    const pending = pendingWrite.current
    if (pending) {
      pending.entries.push({ id, stat })
      return
    }
    pendingWrite.current = {
      entries: [{ id, stat }],
      timer: setTimeout(flushWrite, 120),
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
        id?: unknown
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
      if (data.type === AUTHOR_MSG_GENERATE) {
        void runGenerate(data as {
          id?: unknown
          purpose?: unknown
          messages?: unknown
          systemPrompt?: unknown
          maxTokens?: unknown
        })
        return
      }
      if (data.type === AUTHOR_MSG_SAVE_METADATA && sessionId) {
        const id = String((data as { id?: unknown }).id ?? '')
        const metadata = (data as { metadata?: unknown }).metadata
        if (!id || !metadata || typeof metadata !== 'object') {
          return
        }
        void request(`/api/sessions/${encodeURIComponent(sessionId)}/author/state`, {
          method: 'POST',
          body: JSON.stringify({ chat_metadata: metadata }),
        })
          .then(() => postMetadataResult(id, { ok: true }))
          .catch((error) => {
            postMetadataResult(id, {
              ok: false,
              error: error instanceof Error ? error.message : String(error),
            })
          })
        return
      }
      if (data.type === AUTHOR_MSG_SAVE_MESSAGES && sessionId) {
        const id = String((data as { id?: unknown }).id ?? '')
        const changes = (data as { changes?: unknown }).changes
        const creates = (data as { creates?: unknown }).creates
        if (!id) {
          return
        }
        const changeList = Array.isArray(changes) ? changes : []
        const createList = Array.isArray(creates) ? creates : []
        if (changeList.length === 0 && createList.length === 0) {
          postMessagesResult(id, { ok: true, result: { updated: [], created: [] } })
          return
        }
        beginBusy()
        void request<{
          updated?: string[]
          created?: Array<{ id?: string; message_id?: number; role?: string }>
        }>(`/api/sessions/${encodeURIComponent(sessionId)}/st/messages`, {
          method: 'PATCH',
          body: JSON.stringify({ changes: changeList, creates: createList }),
        })
          .then((result) =>
            postMessagesResult(id, {
              ok: true,
              result: { updated: result.updated ?? [], created: result.created ?? [] },
            })
          )
          .catch((error) => {
            postMessagesResult(id, {
              ok: false,
              error: error instanceof Error ? error.message : String(error),
            })
          })
          .finally(endBusy)
        return
      }
      if (data.type === AUTHOR_MSG_GENERATE_MAIN && sessionId) {
        const id = String((data as { id?: unknown }).id ?? '')
        if (!id) {
          return
        }
        const quiet = String((data as { quiet_prompt?: unknown }).quiet_prompt ?? '')
        const ok = generateCardReply(sessionId, {
          quietPrompt: quiet,
          automaticTrigger: true,
        })
        postGenerateMainResult(id, ok ? { ok: true } : { ok: false, error: 'unavailable' })
        return
      }
      if (data.type === AUTHOR_MSG_PHONE_MAINLINE && sessionId) {
        const text = String((data as { text?: unknown }).text ?? '')
        void request(`/api/sessions/${encodeURIComponent(sessionId)}/author/state`, {
          method: 'POST',
          body: JSON.stringify({ phone_mainline: text }),
        }).catch(() => undefined)
        return
      }
      if (data.type === AUTHOR_MSG_GLOBAL_VARS) {
        void request('/api/st/global-vars', {
          method: 'PUT',
          body: JSON.stringify({ variables: (data as { variables?: unknown }).variables || {} }),
        }).catch(() => undefined)
        return
      }
      if (data.type === AUTHOR_MSG_INJECT_PROMPTS && sessionId) {
        void request(`/api/sessions/${encodeURIComponent(sessionId)}/author/state`, {
          method: 'POST',
          body: JSON.stringify({
            prompts: (data as { prompts?: unknown }).prompts,
            remove_prompts: (data as { remove?: unknown }).remove,
          }),
        }).catch(() => undefined)
        return
      }
      if (data.type === AUTHOR_MSG_COMPOSE && sessionId) {
        setDraftText(sessionId, String(data.text ?? ''))
        return
      }
      if (data.type === AUTHOR_MSG_WRITE && data.stat_data && typeof data.stat_data === 'object') {
        queueWrite(
          typeof data.id === 'string' ? data.id : '',
          data.stat_data as Record<string, unknown>
        )
        return
      }
      if (data.type === AUTHOR_MSG_WRITE_WORLD && canWrite && sessionId && Array.isArray((data as { entries?: unknown }).entries)) {
        beginBusy()
        void writeSessionWorld(
          sessionId,
          (data as { entries: unknown[] }).entries
        )
          .catch(() => {})
          .finally(endBusy)
        return
      }
      if (data.type === AUTHOR_MSG_WRITE_CHARACTER && canWrite && sessionId) {
        const fields = (data as { fields?: unknown }).fields
        if (fields && typeof fields === 'object') {
          beginBusy()
          void writeSessionCharacter(
            sessionId,
            fields as Record<string, unknown>
          )
            .catch(() => {})
            .finally(endBusy)
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
    <div style={{ position: 'relative' }}>
      <iframe
        ref={iframeRef}
        title="酒馆助手脚本面板"
        sandbox="allow-scripts allow-modals allow-forms"
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
    </div>
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
