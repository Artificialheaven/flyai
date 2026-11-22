/**
 * 酒馆卡「整页 HTML + JS」应用的沙箱容器。
 *
 * iframe 只开 `allow-scripts`，没有同源权限：读不到 localStorage、Cookie 与
 * 页面里的 bearer token。网络由 srcDoc 里的 CSP 限制（见 services/st/cardApp.ts），
 * 高度通过 postMessage 自适应，来源与消息类型都校验。
 *
 * 变量是只读快照：初次渲染随 srcDoc 注入，iframe 刷新/就绪时再通过
 * postMessage 重放一次。卡片只能发“意图”，宿主逐次弹窗确认后才执行；
 * 状态面板等没有消息上下文的地方能力全关，卡片保持纯只读。
 */

import clsx from 'clsx'
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { FlyApiError, assetUrl } from '../../services/flyai/client.js'
import {
  clearCardResourceCache,
  readCachedNetProxy,
} from '../../services/flyai/cardResourceCache.js'
import { clearImageProxyCache } from '../../services/st/imageProxy.js'
import {
  proxyNet,
  writeSessionCharacter,
  writeSessionMvu,
  writeSessionWorld,
} from '../../services/flyai/st.js'
import {
  continueReply,
  regenerateReply,
  reloadSession,
  sendCardMessage,
} from '../../services/chat/store.js'
import {
  getDraft,
  setDraftText,
  useDrafts,
} from '../../services/chat/drafts.js'
import {
  CARD_APP_PROTOCOL_VERSION,
  CARD_CODE_ACTION_FAILED,
  CARD_CODE_ACTION_REJECTED,
  CARD_CODE_NET_DENIED,
  CARD_CODE_NET_FAILED,
  CARD_CODE_RATE_LIMITED,
  CARD_CODE_READ_ONLY,
  CARD_CODE_UNAVAILABLE,
  CARD_CODE_VARS_TOO_LARGE,
  CARD_MSG_ACTION,
  CARD_MSG_ACTION_RESULT,
  CARD_MSG_COMPOSE,
  CARD_MSG_DRAFT,
  CARD_MSG_ERROR,
  CARD_MSG_FETCH,
  CARD_MSG_FETCH_ABORT,
  CARD_MSG_FETCH_RESULT,
  CARD_MSG_MESSAGE,
  CARD_MSG_READY,
  CARD_MSG_RESIZE,
  CARD_MSG_TOAST,
  CARD_MSG_VARS,
  CARD_MSG_WRITE_CTX,
  buildCardAppDoc,
  sanitizeCardAppVariables,
  type CardAppCapabilities,
  type CardAppCharacter,
  type CardAppWriteContext,
} from '../../services/st/cardApp.js'
import {
  checkCardIntent,
  createCardActionLimiter,
  MAX_CARD_SEND_TEXT,
  resolveCapabilities,
  type CardActionRateLimiter,
  type CardIntent,
} from '../../services/st/cardActions.js'
import {
  acquireCardConfirm,
  type ConfirmRelease,
  type ConfirmTicket,
} from '../../services/st/cardConfirmQueue.js'
import { useTranslate } from '../../i18n/index.js'
import { IconButton } from '../base/IconButton.js'
import { showToast } from '../base/toastState.js'
import {
  IconCollapseOutline16,
  IconExpandOutline16,
  IconRefreshOutline16,
  IconTrashOutline16,
} from '../base/icons.js'
import { CardActionDialog, type CardConfirmRequest } from '../messages/CardActionDialog.js'
import styles from './CardAppFrame.module.css'

const MIN_HEIGHT = 120
const MAX_HEIGHT = 2400
const INITIAL_HEIGHT = 320

const clampHeight = (value: number): number =>
  Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(value)))

/** 当前消息的变量快照；未启用 MVU 时为 undefined。 */
export const CardAppVariablesContext = createContext<
  Record<string, unknown> | undefined
>(undefined)

export const useCardAppVariables = (): Record<string, unknown> | undefined =>
  useContext(CardAppVariablesContext)

/** 当前角色（名称/头像）；消息列表提供，抽屉等场景可用同名 prop 覆盖。 */
export const CardAppCharacterContext = createContext<CardAppCharacter | undefined>(
  undefined
)

/**
 * 卡内授权动作的宿主上下文。只有消息列表里的消息气泡会提供；
 * 状态面板、抽屉等场景没有上下文，卡片能力全关、保持只读。
 */
export type CardAppHost = {
  sessionId: string
  messageId: string
  isLast: boolean
  editable: boolean
  streaming: boolean
  threadId?: string
  /** 用户设置层面的能力开关；动态授权（末条/流式）在宿主侧再判定 */
  features: CardAppCapabilities
  /** 写入读侧快照（世界书副本、角色白名单字段） */
  writeContext?: CardAppWriteContext | null
}

export const CardAppHostContext = createContext<CardAppHost | undefined>(undefined)

/** 每会话内存级动作限流；刷新页面即重置 */
const sessionLimiters = new Map<string, CardActionRateLimiter>()
const limiterFor = (sessionId: string): CardActionRateLimiter => {
  let limiter = sessionLimiters.get(sessionId)
  if (!limiter) {
    limiter = createCardActionLimiter()
    sessionLimiters.set(sessionId, limiter)
  }
  return limiter
}

/** 本次页面会话内已确认过的网络目标（按会话）；刷新即失效，后端仍是最终闸门 */
const approvedNetHosts = new Map<string, Set<string>>()

const MAX_NET_BODY_BYTES = 256 * 1024
const NET_METHODS = new Set(['GET', 'HEAD', 'POST'])

/** base64 文本对应的原始字节数（粗略即可，用于展示与上限预检） */
const base64Bytes = (text: unknown): number => {
  if (typeof text !== 'string' || !text) {
    return 0
  }
  const padding = text.endsWith('==') ? 2 : text.endsWith('=') ? 1 : 0
  return Math.max(0, Math.floor((text.length * 3) / 4) - padding)
}

/** 只透传 Accept/Content-Type；其余请求头由服务端丢弃 */
const pickNetHeaders = (raw: unknown): Record<string, string> | undefined => {
  if (!raw || typeof raw !== 'object') {
    return undefined
  }
  const out: Record<string, string> = {}
  for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
    const lower = name.toLowerCase()
    if ((lower === 'accept' || lower === 'content-type') && typeof value === 'string') {
      out[lower] = value
    }
  }
  return Object.keys(out).length > 0 ? out : undefined
}

/** 头像可能是资源路径、相对 URL 或外链：统一解析为绝对地址供 CSP/卡内使用 */
const resolveAvatar = (avatar?: string | null): string => {
  if (!avatar) {
    return ''
  }
  if (/^(data:|https?:)/i.test(avatar)) {
    return avatar
  }
  try {
    return new URL(assetUrl(avatar), window.location.href).href
  } catch (err) {
    return assetUrl(avatar)
  }
}

type ConfirmEntry = {
  request: CardConfirmRequest
  resolve: (ok: boolean) => void
  /** 用户勾选“允许后续所有请求”时执行（仅网络确认） */
  onAllowAll?: () => void
  release: ConfirmRelease
}

export const CardAppFrame = memo(function CardAppFrame({
  html,
  message,
  variables,
  externalMedia,
  character,
  showToolbar = true,
  chromeless = false,
}: {
  html: string
  /** 当前消息原文：卡内脚本通过 getChatMessages 读取 */
  message: string
  /** 当前消息的 stat_data；只读快照 */
  variables?: Record<string, unknown> | null
  externalMedia: boolean
  /** 当前角色；不传则读 CardAppCharacterContext */
  character?: CardAppCharacter | null
  /** 面板等嵌入场景可隐藏工具栏 */
  showToolbar?: boolean
  /** 去掉宿主内边距与背景，交给外层容器 */
  chromeless?: boolean
}) {
  const t = useTranslate()
  const host = useContext(CardAppHostContext)
  const contextCharacter = useContext(CardAppCharacterContext)
  const [height, setHeight] = useState(INITIAL_HEIGHT)
  const [reloadKey, setReloadKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [confirmRequest, setConfirmRequest] = useState<CardConfirmRequest | null>(null)
  const [errors, setErrors] = useState<{ count: number; last: string }>({
    count: 0,
    last: '',
  })
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const cardHostRef = useRef<CardAppHost | undefined>(host)
  const pendingFetchesRef = useRef(new Map<string, AbortController>())
  const currentConfirmRef = useRef<ConfirmEntry | null>(null)
  const confirmTicketsRef = useRef(new Set<ConfirmTicket>())
  const mountedRef = useRef(true)

  useLayoutEffect(() => {
    cardHostRef.current = host
  }, [host])

  const sanitized = useMemo(() => sanitizeCardAppVariables(variables), [variables])
  const sourceCharacter = character ?? contextCharacter
  const characterName = sourceCharacter?.name ?? ''
  const characterAvatar = sourceCharacter?.avatar ?? ''
  const resolvedCharacter = useMemo<CardAppCharacter | undefined>(() => {
    if (!characterName && !characterAvatar) {
      return undefined
    }
    return { name: characterName, avatar: resolveAvatar(characterAvatar) }
  }, [characterName, characterAvatar])

  // 能力开关是“用户设置”层面：随 doc 固化进 shim，避免流式开关切换时重建 iframe。
  // 动态授权（末条/非流式/可编辑）在每次意图到达时由宿主重新判定。
  const capActions = host?.features.actions === true
  const capWrite = host?.features.write === true
  const capNet = host?.features.net === true
  const interactive = capActions || capWrite || capNet

  // 变量/消息变化不重建 doc：重建会重载 iframe，卡内状态与滚动位置都会丢。
  // 刷新时从 ref 取最新值，普通更新只经 postMessage 补发。
  const latestRef = useRef({ message, variables: sanitized.variables })
  useLayoutEffect(() => {
    latestRef.current = { message, variables: sanitized.variables }
  }, [message, sanitized.variables])

  const doc = useMemo(() => {
    const latest = latestRef.current
    return buildCardAppDoc(html, {
      message: latest.message,
      variables: latest.variables,
      externalMedia,
      character: resolvedCharacter,
      capabilities: { actions: capActions, write: capWrite, net: capNet },
    })
  }, [html, externalMedia, reloadKey, resolvedCharacter, capActions, capWrite, capNet])

  const postToCard = useCallback((payload: Record<string, unknown>) => {
    const win = iframeRef.current?.contentWindow
    if (!win) {
      return
    }
    try {
      win.postMessage({ v: CARD_APP_PROTOCOL_VERSION, ...payload }, '*')
    } catch {
      // iframe 已卸载或仍是不透明源，忽略
    }
  }, [])

  const sendContext = useCallback(() => {
    postToCard({ type: CARD_MSG_VARS, variables: sanitized.variables })
    postToCard({ type: CARD_MSG_MESSAGE, message })
  }, [message, postToCard, sanitized])

  // 世界书/角色快照可能很大：只在能力开启且内容变化时补发，不随每次 token 更新重发。
  const writeCtxJson = useMemo(() => {
    if (!host || !host.features.write) {
      return ''
    }
    try {
      return JSON.stringify(host.writeContext ?? {})
    } catch {
      return ''
    }
  }, [host])

  const sendWriteContext = useCallback(() => {
    if (!writeCtxJson) {
      return
    }
    try {
      postToCard({
        type: CARD_MSG_WRITE_CTX,
        context: JSON.parse(writeCtxJson) as CardAppWriteContext,
      })
    } catch {
      // 序列化失败时保持卡片只拿到空快照
    }
  }, [postToCard, writeCtxJson])

  useEffect(() => {
    sendWriteContext()
  }, [sendWriteContext])

  // 输入框草稿同步给卡内兼容层：追加模式要基于用户当前输入，而不是空文本。
  const sendDraft = useCallback(() => {
    const sessionId = cardHostRef.current?.sessionId
    if (!sessionId) {
      return
    }
    postToCard({ type: CARD_MSG_DRAFT, text: getDraft(sessionId).text })
  }, [postToCard])

  useEffect(() => {
    if (!host?.sessionId) {
      return
    }
    sendDraft()
    // 用户在主输入框打字/切换会话时也同步；非本会话的草稿变化只是多一次广播
    return useDrafts.subscribe(sendDraft)
  }, [host?.sessionId, sendDraft])

  /**
   * 弹窗确认：全局闸门保证同一时刻只有一个卡片确认弹窗。
   * 同一张卡连续请求、页面上多张卡同时请求，都按到达顺序逐个弹出。
   */
  const askConfirm = useCallback(
    (
      request: CardConfirmRequest,
      options?: { onAllowAll?: () => void; skipIf?: () => boolean }
    ): Promise<boolean> =>
      new Promise((resolve) => {
        const ticket = acquireCardConfirm()
        confirmTicketsRef.current.add(ticket)
        ticket.ready
          .then((release) => {
            confirmTicketsRef.current.delete(ticket)
            if (!mountedRef.current) {
              release()
              resolve(false)
              return
            }
            // 排队期间可能已有同源请求被允许（或勾选了“允许后续所有”），
            // 直接放行，不再弹第二次
            if (options?.skipIf?.()) {
              release()
              resolve(true)
              return
            }
            currentConfirmRef.current = {
              request,
              resolve,
              onAllowAll: options?.onAllowAll,
              release,
            }
            setConfirmRequest(request)
          })
          .catch(() => {
            confirmTicketsRef.current.delete(ticket)
            resolve(false)
          })
      }),
    []
  )

  const finishConfirm = useCallback((ok: boolean, allowAll = false) => {
    const current = currentConfirmRef.current
    if (!current) {
      return
    }
    currentConfirmRef.current = null
    setConfirmRequest(null)
    if (ok && allowAll) {
      current.onAllowAll?.()
    }
    current.resolve(ok)
    current.release()
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      for (const ticket of confirmTicketsRef.current) {
        ticket.cancel()
      }
      confirmTicketsRef.current.clear()
      const current = currentConfirmRef.current
      currentConfirmRef.current = null
      current?.resolve(false)
      current?.release()
      for (const controller of pendingFetchesRef.current.values()) {
        controller.abort()
      }
      pendingFetchesRef.current.clear()
    }
  }, [])

  const runIntent = useCallback(
    async (
      intent: CardIntent,
      target: CardAppHost
    ): Promise<{ ok: true } | { ok: false; error: string }> => {
      if (intent.kind === 'action') {
        // 仅聊天动作限流；变量/世界书写入往往是卡片的多字段更新，不能打断
        if (!limiterFor(target.sessionId).tryAcquire(Date.now())) {
          showToast(t('cardActionRateLimited'))
          return { ok: false, error: CARD_CODE_RATE_LIMITED }
        }
      }
      const allowed = await askConfirm({ kind: 'intent', intent })
      if (!allowed) {
        return { ok: false, error: CARD_CODE_ACTION_REJECTED }
      }
      try {
        if (intent.kind === 'action') {
          let started = false
          if (intent.action === 'send') {
            started = sendCardMessage(target.sessionId, intent.text ?? '')
            // 卡片通常是先填输入框再发；发送成功后清掉同一份草稿，避免重复发送
            if (started && getDraft(target.sessionId).text === (intent.text ?? '')) {
              setDraftText(target.sessionId, '')
            }
          } else if (intent.action === 'continue') {
            started = continueReply(target.sessionId, target.messageId)
          } else {
            started = regenerateReply(target.sessionId, target.messageId)
          }
          if (!started) {
            showToast(t('cardActionUnavailable'))
            return { ok: false, error: CARD_CODE_UNAVAILABLE }
          }
          return { ok: true }
        }
        if (intent.kind === 'vars') {
          await writeSessionMvu(
            target.sessionId,
            intent.mode === 'patch'
              ? { mode: 'patch', ops: intent.ops, thread_id: target.threadId }
              : { mode: 'replace', stat_data: intent.stat_data, thread_id: target.threadId }
          )
        } else if (intent.kind === 'world') {
          await writeSessionWorld(target.sessionId, intent.entries)
        } else {
          await writeSessionCharacter(target.sessionId, intent.fields)
        }
        await reloadSession(target.sessionId)
        return { ok: true }
      } catch (err) {
        const status = err instanceof FlyApiError ? err.status : 0
        const unavailable = status === 403 || status === 409
        showToast(t(unavailable ? 'cardActionUnavailable' : 'cardActionFailed'))
        return {
          ok: false,
          error: unavailable ? CARD_CODE_UNAVAILABLE : CARD_CODE_ACTION_FAILED,
        }
      }
    },
    [askConfirm, t]
  )

  const handleAction = useCallback(
    async (data: { id?: unknown; intent?: unknown }) => {
      const id = data.id
      if (typeof id !== 'string') {
        return
      }
      const reply = (payload: { ok: boolean; error?: string; result?: unknown }) =>
        postToCard({ type: CARD_MSG_ACTION_RESULT, id, ...payload })
      const target = cardHostRef.current
      if (!target) {
        reply({ ok: false, error: CARD_CODE_UNAVAILABLE })
        return
      }
      const resolved = resolveCapabilities(target.features, target)
      const checked = checkCardIntent(data.intent, resolved)
      if (!checked.ok) {
        showToast(
          t(checked.code === CARD_CODE_UNAVAILABLE ? 'cardActionUnavailable' : 'cardActionFailed')
        )
        reply({ ok: false, error: checked.code })
        return
      }
      const outcome = await runIntent(checked.intent, target)
      reply(outcome)
    },
    [postToCard, runIntent, t]
  )

  const handleFetch = useCallback(
    async (data: {
      id?: unknown
      request?: {
        method?: unknown
        url?: unknown
        headers?: unknown
        bodyBase64?: unknown
      } | null
    }) => {
      const id = data.id
      if (typeof id !== 'string') {
        return
      }
      const reply = (payload: Record<string, unknown>) =>
        postToCard({ type: CARD_MSG_FETCH_RESULT, id, ...payload })
      const deny = () => reply({ ok: false, error: CARD_CODE_NET_DENIED })
      const target = cardHostRef.current
      if (!target) {
        deny()
        return
      }
      const resolved = resolveCapabilities(target.features, target)
      if (!resolved.net) {
        deny()
        return
      }
      const raw = data.request
      const method = String(raw?.method ?? 'GET').toUpperCase()
      if (!NET_METHODS.has(method)) {
        deny()
        return
      }
      let url: URL
      try {
        url = new URL(String(raw?.url ?? ''))
      } catch {
        deny()
        return
      }
      if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        deny()
        return
      }
      const bodyBase64 = typeof raw?.bodyBase64 === 'string' ? raw.bodyBase64 : ''
      const bodySize = base64Bytes(bodyBase64)
      if (method !== 'POST' && bodyBase64) {
        deny()
        return
      }
      if (bodySize > MAX_NET_BODY_BYTES) {
        reply({ ok: false, error: CARD_CODE_NET_FAILED })
        return
      }
      const netHeaders = pickNetHeaders(raw?.headers)
      // 本地已缓存过的静态资源：不发请求，也不再弹网络确认
      const cached = await readCachedNetProxy({
        method,
        url: url.href,
        headers: netHeaders,
      })
      if (cached) {
        reply({
          ok: true,
          status: cached.status,
          headers: cached.headers,
          bodyBase64: cached.body_base64,
          truncated: cached.truncated,
        })
        return
      }
      // 首次访问某站点时确认；同一会话内后续请求不再重复弹窗
      let approved = approvedNetHosts.get(target.sessionId)
      if (!approved) {
        approved = new Set()
        approvedNetHosts.set(target.sessionId, approved)
      }
      if (!approved.has('*') && !approved.has(url.origin)) {
        const allowed = await askConfirm(
          {
            kind: 'net',
            method,
            url: url.href,
            bodySize,
          },
          {
            // 勾选“允许后续所有请求”：本会话内所有来源都不再逐个确认
            onAllowAll: () => approved.add('*'),
            // 排队期间同源已被允许，或已勾选允许全部：直接放行
            skipIf: () => approved.has('*') || approved.has(url.origin),
          }
        )
        if (!allowed) {
          reply({ ok: false, error: CARD_CODE_ACTION_REJECTED })
          return
        }
        approved.add(url.origin)
      }
      const controller = new AbortController()
      pendingFetchesRef.current.set(id, controller)
      try {
        const result = await proxyNet(
          {
            method: method as 'GET' | 'HEAD' | 'POST',
            url: url.href,
            headers: netHeaders,
            body_base64: method === 'POST' ? bodyBase64 : undefined,
          },
          controller.signal
        )
        reply({
          ok: true,
          status: result.status,
          headers: result.headers,
          bodyBase64: result.body_base64,
          truncated: result.truncated,
        })
      } catch (err) {
        // 卡内超时/主动取消时 shim 已自行 reject，这里不再回包
        if (controller.signal.aborted) {
          return
        }
        const status = err instanceof FlyApiError ? err.status : 0
        const denied = status === 400 || status === 403 || status === 404
        reply({ ok: false, error: denied ? CARD_CODE_NET_DENIED : CARD_CODE_NET_FAILED })
      } finally {
        pendingFetchesRef.current.delete(id)
      }
    },
    [askConfirm, postToCard]
  )

  // 只接受自己 iframe 发来的消息，其它来源一律忽略
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return
      }
      const data = event.data as {
        type?: unknown
        height?: unknown
        level?: unknown
        text?: unknown
        code?: unknown
        id?: unknown
        intent?: unknown
        request?: {
          method?: unknown
          url?: unknown
          headers?: unknown
          bodyBase64?: unknown
        } | null
      } | null
      if (!data || typeof data !== 'object') {
        return
      }
      if (data.type === CARD_MSG_RESIZE) {
        const next = Number(data.height)
        if (Number.isFinite(next)) {
          setHeight(clampHeight(next))
        }
        return
      }
      if (data.type === CARD_MSG_READY) {
        sendContext()
        sendWriteContext()
        sendDraft()
        return
      }
      if (data.type === CARD_MSG_ERROR) {
        const text = typeof data.text === 'string' ? data.text.trim() : ''
        setErrors((prev) => ({ count: prev.count + 1, last: text || prev.last }))
        return
      }
      if (data.type === CARD_MSG_TOAST) {
        if (data.code === CARD_CODE_READ_ONLY) {
          showToast(t('cardAppReadOnlyHint'))
          return
        }
        if (data.code === CARD_CODE_VARS_TOO_LARGE) {
          showToast(t('cardAppVariablesTooLarge'))
          return
        }
        const text = typeof data.text === 'string' ? data.text.trim() : ''
        if (text) {
          showToast(text)
        }
        return
      }
      if (data.type === CARD_MSG_COMPOSE) {
        const target = cardHostRef.current
        const text = typeof data.text === 'string' ? data.text : ''
        if (target?.editable && text.length <= MAX_CARD_SEND_TEXT) {
          setDraftText(target.sessionId, text)
        }
        return
      }
      if (data.type === CARD_MSG_ACTION) {
        void handleAction(data)
        return
      }
      if (data.type === CARD_MSG_FETCH) {
        void handleFetch(data)
        return
      }
      if (data.type === CARD_MSG_FETCH_ABORT) {
        const id = data.id
        if (typeof id === 'string') {
          pendingFetchesRef.current.get(id)?.abort()
        }
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [handleAction, handleFetch, sendContext, sendDraft, sendWriteContext, t])

  // 变量超限时提示一次：卡片只能拿到空状态
  const truncatedRef = useRef(false)
  useEffect(() => {
    if (sanitized.truncated && !truncatedRef.current && showToolbar) {
      truncatedRef.current = true
      showToast(t('cardAppVariablesTooLarge'))
    }
  }, [sanitized.truncated, showToolbar, t])

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreen(document.fullscreenElement === hostRef.current)
    }
    document.addEventListener('fullscreenchange', onFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange)
  }, [])

  const toggleFullscreen = useCallback(() => {
    const hostEl = hostRef.current
    if (!hostEl) {
      return
    }
    if (document.fullscreenElement === hostEl) {
      void document.exitFullscreen().catch(() => undefined)
    } else {
      void hostEl.requestFullscreen?.().catch(() => undefined)
    }
  }, [])

  const handleLoad = useCallback(() => {
    sendContext()
    sendWriteContext()
    sendDraft()
  }, [sendContext, sendDraft, sendWriteContext])

  if (!doc) {
    return null
  }

  return (
    <div
      ref={hostRef}
      className={clsx(
        styles.host,
        chromeless && styles.chromeless,
        fullscreen && styles.fullscreen
      )}
    >
      {showToolbar && !chromeless ? (
        <div className={styles.toolbar}>
          <span className={styles.labelRow}>
            <span className={styles.label}>{t('cardAppLabel')}</span>
            <span
              className={clsx(styles.badge, interactive && styles.badgeActive)}
              title={t(interactive ? 'cardAppInteractiveHint' : 'cardAppReadOnlyHint')}
            >
              {t(interactive ? 'cardAppInteractive' : 'cardAppReadOnly')}
            </span>
            {errors.count > 0 ? (
              <span
                className={styles.errorBadge}
                title={errors.last || t('cardAppErrorBadge', { count: errors.count })}
              >
                {t('cardAppErrorBadge', { count: errors.count })}
              </span>
            ) : null}
          </span>
          <div className={styles.actions}>
            <IconButton
              icon={<IconTrashOutline16 />}
              size="xs"
              tooltip={t('cardAppClearCache')}
              onClick={() => {
                void (async () => {
                  clearImageProxyCache()
                  await clearCardResourceCache()
                  showToast(t('cardAppCacheCleared'))
                  setReloadKey((key) => key + 1)
                })()
              }}
            />
            <IconButton
              icon={<IconRefreshOutline16 />}
              size="xs"
              tooltip={t('cardAppRefresh')}
              onClick={() => {
                setErrors({ count: 0, last: '' })
                setReloadKey((key) => key + 1)
              }}
            />
            <IconButton
              icon={fullscreen ? <IconCollapseOutline16 /> : <IconExpandOutline16 />}
              size="xs"
              tooltip={t(fullscreen ? 'cardAppCollapse' : 'cardAppExpand')}
              onClick={toggleFullscreen}
            />
          </div>
        </div>
      ) : null}
      <iframe
        key={reloadKey}
        ref={iframeRef}
        className={styles.frame}
        style={fullscreen ? undefined : { height }}
        title={t('cardAppLabel')}
        sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
        srcDoc={doc}
        loading="lazy"
        referrerPolicy="no-referrer"
        onLoad={handleLoad}
      />
      {confirmRequest ? (
        <CardActionDialog
          request={confirmRequest}
          onConfirm={(options) => finishConfirm(true, options?.allowAll === true)}
          onCancel={() => finishConfirm(false)}
        />
      ) : null}
    </div>
  )
})
