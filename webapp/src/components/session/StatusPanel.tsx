/**
 * 酒馆卡状态面板：把卡内「占位符 → HTML 面板」的展示脚本渲染成可独立查看的
 * 抽屉 / 全屏页，并提供按消息快照回看与 stat_data 键值兜底。
 *
 * 抽屉/全屏在用户开启 JS 沙箱时，把面板放进只读沙箱 iframe 执行卡内脚本，
 * 变量变化经 postMessage 刷新且不重载 iframe；消息下方内联面板与键值兜底
 * 仍走 SanitizedHtml 白名单清洗，不执行脚本。
 */

import clsx from 'clsx'
import { useContext, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../app/viewport.js'
import {
  useTranslate,
  type TranslationKey,
  type TranslationParams,
} from '../../i18n/index.js'
import { getVisibleMessages } from '../../services/chat/branches.js'
import { request } from '../../services/flyai/client.js'
import type { Session } from '../../services/chat/types.js'
import type {
  CardWriteContext,
  RegexScript,
  StAuthorPayload,
  StPanel,
} from '../../services/flyai/types.js'
import {
  adaptHtmlScripts,
  authorRuntimeMessages,
  type AuthorRuntimeMessage,
} from '../../services/st/authorRuntime.js'
import {
  stripAppFence,
  type CardAppCharacter,
} from '../../services/st/cardApp.js'
import {
  renderPanel,
  statDataRows,
  withoutAuthorRemovals,
} from '../../services/st/panel.js'
import { setPanelAutoAppend, usePanelPrefs } from '../../services/st/panelPrefs.js'
import { IconButton } from '../base/IconButton.js'
import { Select } from '../base/Select.js'
import {
  IconCloseOutline16,
  IconCollapseOutline16,
  IconExpandOutline16,
} from '../base/icons.js'
import { useOverlayClose } from '../base/overlayStack.js'
import {
  CardAppFrame,
  CardAppHostContext,
  type CardAppHost,
} from '../markdown/CardAppFrame.js'
import { AuthorPanelFrame } from './AuthorPanelFrame.js'
import { MarkdownMacroNamesContext } from '../markdown/Markdown.js'
import {
  MarkdownRenderOptionsContext,
  SanitizedHtml,
  type MarkdownRenderOptions,
} from '../markdown/SanitizedHtml.js'
import styles from './StatusPanel.module.css'

/** 快照选择器里「当前状态」的取值 */
const CURRENT = ''

type Snapshot = { id: string; label: string }

const hasData = (data: unknown): data is Record<string, unknown> =>
  !!data && typeof data === 'object' && Object.keys(data).length > 0

/** 面板 HTML 的渲染策略（与消息正文一致：开关关闭时保持转义文本行为） */
const panelOptions = (session: Session): MarkdownRenderOptions => {
  const st = session.st
  return {
    htmlEnabled: !!st?.html?.enabled && st?.flags?.html !== false,
    externalImages: !!st?.html?.external_images,
    imageProxy: !!st?.html?.image_proxy && !!st?.flags?.image_proxy,
    // 抽屉/全屏与消息下方的内联面板都可运行卡内脚本（仍由用户 JS 沙箱开关控制），
    // 这样带脚本的卡片面板才能渲染成完整界面，而不是退化成静态文本。
    jsSandbox: st?.flags?.js_sandbox === true,
  }
}

/** 会话是否值得显示状态面板入口：卡内面板模板，或原生 MVU 变量 */
export const statusPanelAvailable = (session: Session): boolean => {
  const st = session.st
  if (!st) {
    return false
  }
  if ((st.panels?.length ?? 0) > 0) {
    return true
  }
  // 作者脚本自己会挂载面板（悬浮球/全息终端），没有占位符也要给入口
  if (st.author?.enabled !== false && (st.author?.scripts?.length ?? 0) > 0) {
    return true
  }
  return st.flags?.mvu !== false && !!st.mvu?.enabled && hasData(st.mvu.stat_data)
}

const snapshotsOf = (
  session: Session,
  t: (key: TranslationKey, params?: TranslationParams) => string
): Snapshot[] => {
  const out: Snapshot[] = []
  getVisibleMessages(session).forEach((message) => {
    if (message.role !== 'assistant' || !hasData(message.statData)) {
      return
    }
    out.push({ id: message.id, label: t('statusPanelSnapshot', { index: out.length + 1 }) })
  })
  return out.reverse()
}

const PanelBody = ({
  panels,
  statData,
  options,
  scripts,
  cardCharacter,
  author,
  authorMessages,
  sessionId,
  threadId,
  userName,
  canWrite = false,
  canAct = false,
  actionMessageId,
  writeContext,
  streaming = false,
  netEnabled = false,
  runScripts = false,
}: {
  panels: StPanel[]
  statData: Record<string, unknown> | undefined
  options: MarkdownRenderOptions
  scripts: readonly RegexScript[] | undefined
  /** 沙箱面板可读取的角色名/头像（抽屉在消息列表之外，需显式传入） */
  cardCharacter?: CardAppCharacter
  /** 酒馆助手作者脚本（TavernHelper）；有脚本时面板改走作者运行时 */
  author?: StAuthorPayload
  /** 作者运行时的消息镜像（不含分支外的消息） */
  authorMessages?: AuthorRuntimeMessage[]
  /** 面板沙箱的网络代理需要会话 id 计账 */
  sessionId?: string
  /** 私聊窗口 id；作者脚本写入变量时回传 */
  threadId?: string
  /** 玩家名；作者脚本 context.name1 / 手机署名 */
  userName?: string
  /** 作者脚本是否可写回会话副本（st_card_write） */
  canWrite?: boolean
  /** 作者脚本是否可发送/继续/重生成（st_card_actions） */
  canAct?: boolean
  /** 继续/重生成的目标消息 id */
  actionMessageId?: string
  /** 世界书/角色副本读取快照 */
  writeContext?: CardWriteContext
  /** 当前会话是否正在生成 */
  streaming?: boolean
  /** 卡片网络代理开关；面板只借网络能力渲染图片，不授予动作/写入 */
  netEnabled?: boolean
  /** 为真时面板放进沙箱 iframe 执行卡内脚本；内联面板同样需要渲染卡片 */
  runScripts?: boolean
}) => {
  const t = useTranslate()
  const macroNames = useContext(MarkdownMacroNamesContext)
  const htmlEnabled = options.htmlEnabled
  // 消息正文会加入“移除作者面板占位符”的合成脚本；状态面板渲染必须用
  // 原始正则，否则占位符会先被删空，面板永远拿不到 HTML。
  const panelScripts = useMemo(() => withoutAuthorRemovals(scripts), [scripts])
  const cardVariables = useMemo(
    () => (statData ? { stat_data: statData } : undefined),
    [statData]
  )
  // 面板是只读视图：只保留网络能力（图片/头像），动作与写回一律关闭。
  const panelHost = useMemo<CardAppHost | undefined>(() => {
    if (!sessionId) {
      return undefined
    }
    return {
      sessionId,
      messageId: '',
      // editable 只用于放行网络代理；动作靠 isLast=false、写入靠 features.write=false 关掉
      isLast: false,
      editable: true,
      streaming: false,
      features: { actions: false, write: false, net: netEnabled },
    }
  }, [sessionId, netEnabled])
  const authorScripts = author?.enabled !== false ? author?.scripts ?? [] : []
  const hasAuthorScripts = authorScripts.length > 0
  // 没有占位符面板时，作者脚本本身就是一个面板：脚本会自己往文档里挂载
  // 悬浮球/全息终端。给它一个合成条目，让抽屉能渲染 AuthorPanelFrame。
  const effectivePanels = useMemo<StPanel[]>(() => {
    if (panels.length > 0) {
      return panels
    }
    return hasAuthorScripts
      ? [{ token: '', title: t('statusPanelTitle') } as StPanel]
      : []
  }, [panels, hasAuthorScripts, t])
  const rendered = useMemo(() => {
    if (!htmlEnabled || effectivePanels.length === 0 || (!statData && !hasAuthorScripts)) {
      return []
    }
    const variables = { stat_data: statData ?? {} }
    return effectivePanels
      .map((panel) => {
        const html = renderPanel(
          panel.token, panelScripts, variables, macroNames
        )
        const unmatched = !html.trim() || html.trim() === panel.token.trim()
        // 整页 HTML+JS 应用（带 ```html 围栏/完整文档）走 CardAppFrame；
        // 其余作者面板交给 TavernHelper 运行时（脚本自己找挂载点）。
        const stripped = stripAppFence(html)
        // 不用 isCardAppReplacement 的 512KB 上限：面板替换串会展开变量宏，
        // 实际体积可能远超卡片的静态模板，但仍然是同一种整页应用。
        const app =
          !unmatched &&
          /<script[\s>]/i.test(stripped) &&
          /<!doctype\s+html|<html[\s>]|<body[\s>]/i.test(stripped)
        const authorPanel = hasAuthorScripts && panel.author !== false && !app
        return {
          key: panel.token,
          title: (panel.title || '').trim() || t('statusPanelTitle'),
          html: unmatched ? '' : html,
          author: authorPanel,
          app,
        }
      })
      .filter((item) => item.html.trim() || item.author)
  }, [htmlEnabled, statData, hasAuthorScripts, effectivePanels, panelScripts, macroNames, t])
  const rows = useMemo(() => (statData ? statDataRows(statData) : []), [statData])
  const [globalVariables, setGlobalVariables] = useState<Record<string, unknown> | null>(null)
  useEffect(() => {
    let cancelled = false
    void request<{ variables: Record<string, unknown> }>('/api/st/global-vars')
      .then((data) => {
        if (!cancelled) setGlobalVariables(data.variables || {})
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [sessionId])

  if (rendered.length > 0) {
    return (
      <MarkdownRenderOptionsContext.Provider value={options}>
        <div className={styles.body}>
          {/* 只有真正带 <script> 的面板才进沙箱；静态宏面板继续用清洗后的 HTML，
              避免每次变量更新都重建 iframe。 */}
          {rendered.map((item) => (
            <section key={item.key} className={styles.panel}>
              <div className={styles.panelTitle}>{item.title}</div>
              {runScripts && item.author ? (
                <AuthorPanelFrame
                  html={item.html || ''}
                  author={{ ...author, scripts: authorScripts }}
                  messages={authorMessages ?? []}
                  variables={statData}
                  chatMetadata={author?.chat_metadata}
                  character={author?.character ?? cardCharacter}
                  externalMedia={options.externalImages}
                  sessionId={sessionId}
                  threadId={threadId}
                  userName={userName}
                  canWrite={canWrite}
                  canAct={canAct}
                  actionMessageId={actionMessageId}
                  writeContext={writeContext}
                  globalVariables={globalVariables}
                  streaming={streaming}
                />
              ) : runScripts && options.jsSandbox && (item.app || /<script[\s>]/i.test(item.html)) ? (
                <CardAppHostContext.Provider value={panelHost}>
                  <CardAppFrame
                    html={item.app ? adaptHtmlScripts(item.html) : item.html}
                    message=""
                    variables={cardVariables}
                    externalMedia={options.externalImages}
                    character={cardCharacter}
                    showToolbar={false}
                    chromeless
                  />
                </CardAppHostContext.Provider>
              ) : (
                <SanitizedHtml value={item.html} />
              )}
            </section>
          ))}
        </div>
      </MarkdownRenderOptionsContext.Provider>
    )
  }
  return (
    <div className={styles.body}>
      {panels.length > 0 ? (
        <div className={styles.hint}>
          {t(htmlEnabled ? 'statusPanelFallbackHint' : 'statusPanelHtmlOff')}
        </div>
      ) : null}
      {rows.length > 0 ? (
        <dl className={styles.statList}>
          {rows.map((row) => (
            <div key={row.key} className={styles.statRow}>
              <dt>{row.key}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className={styles.empty}>{t('statusPanelEmpty')}</div>
      )}
    </div>
  )
}

/** 面板主体（抽屉 / 全屏共用） */
export const StatusPanelContent = ({
  session,
  snapshotId,
  runScripts = true,
}: {
  session: Session
  snapshotId: string
  /** 抽屉/全屏默认执行卡内脚本；切快照只更新变量，不重载 iframe */
  runScripts?: boolean
}) => {
  const st = session.st
  const options = useMemo(() => panelOptions(session), [session.st])
  const cardCharacter = useMemo<CardAppCharacter | undefined>(() => {
    const name = session.characterName
    const avatar = session.characterAvatar
    return name || avatar ? { name, avatar } : undefined
  }, [session.characterName, session.characterAvatar])
  const statData = useMemo(() => {
    if (snapshotId === CURRENT) {
      return st?.mvu?.stat_data
    }
    const message = getVisibleMessages(session).find((item) => item.id === snapshotId)
    return message && message.role === 'assistant' ? message.statData : undefined
  }, [session, snapshotId, st])
  const visibleMessages = useMemo(() => getVisibleMessages(session), [session])
  const authorMessages = useMemo(
    () => authorRuntimeMessages(visibleMessages),
    [visibleMessages]
  )
  const authorStreaming = useMemo(
    () =>
      visibleMessages.some(
        (message) => message.role === 'assistant' && message.status === 'streaming'
      ),
    [visibleMessages]
  )
  const lastAssistantId = useMemo(() => {
    for (let index = visibleMessages.length - 1; index >= 0; index -= 1) {
      if (visibleMessages[index].role === 'assistant') {
        return visibleMessages[index].id
      }
    }
    return undefined
  }, [visibleMessages])
  return (
    <PanelBody
      panels={st?.panels ?? []}
      statData={hasData(statData) ? statData : undefined}
      options={options}
      scripts={st?.regex_scripts}
      cardCharacter={cardCharacter}
      author={st?.author}
      authorMessages={authorMessages}
      sessionId={session.id}
      threadId={session.threadId}
      userName={session.userName}
      canWrite={st?.flags?.card_write === true}
      canAct={st?.flags?.card_actions === true}
      actionMessageId={lastAssistantId}
      writeContext={st?.write_context}
      streaming={authorStreaming}
      netEnabled={st?.flags?.net_proxy === true}
      runScripts={runScripts}
    />
  )
}

/** 面板抽屉（移动端底部弹出，桌面端右侧滑出）；可切换全屏 */
export const StatusPanelDrawer = ({
  session,
  onClose,
}: {
  session: Session
  onClose: () => void
}) => {
  const t = useTranslate()
  const isMobile = useIsMobile()
  const autoAppend = usePanelPrefs((state) => state.autoAppend)
  const [snapshotId, setSnapshotId] = useState<string>(CURRENT)
  const [full, setFull] = useState(false)
  useOverlayClose(true, onClose)
  const snapshots = useMemo(() => snapshotsOf(session, t), [session, t])
  const options = useMemo(
    () => [
      { value: CURRENT, label: t('statusPanelCurrent') },
      ...snapshots.map((snapshot) => ({ value: snapshot.id, label: snapshot.label })),
    ],
    [snapshots, t]
  )
  const title = session.st?.panels?.[0]?.title?.trim() || t('statusPanelTitle')
  return createPortal(
    <div
      className={clsx(styles.layer, full && styles.layerFull)}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className={styles.backdrop} onClick={onClose} />
      <div
        className={clsx(
          styles.drawer,
          isMobile && styles.sheet,
          full && styles.drawerFull
        )}
      >
        <div className={styles.header}>
          <div className={styles.title}>{title}</div>
          {snapshots.length > 0 ? (
            <Select
              value={snapshotId}
              options={options}
              onChange={setSnapshotId}
              ariaLabel={t('statusPanelTitle')}
              className={styles.select}
            />
          ) : null}
          <IconButton
            icon={full ? <IconCollapseOutline16 /> : <IconExpandOutline16 />}
            tooltip={t(full ? 'statusPanelExitFullscreen' : 'statusPanelFullscreen')}
            aria-label={t(full ? 'statusPanelExitFullscreen' : 'statusPanelFullscreen')}
            onClick={() => setFull((value) => !value)}
          />
          <IconButton
            icon={<IconCloseOutline16 />}
            tooltip={t('statusPanelClose')}
            aria-label={t('statusPanelClose')}
            onClick={onClose}
          />
        </div>
        <div className={styles.scroll}>
          <StatusPanelContent session={session} snapshotId={snapshotId} />
        </div>
        <div className={styles.footer}>
          <button
            type="button"
            role="switch"
            aria-checked={autoAppend}
            className={clsx(styles.toggle, autoAppend && styles.toggleOn)}
            onClick={() => setPanelAutoAppend(!autoAppend)}
          >
            <span className={styles.toggleTrack}>
              <span className={styles.toggleThumb} />
            </span>
            <span>{t('statusPanelAutoAppend')}</span>
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

/**
 * 开关 C：在每条助手回复下方追加该消息快照对应的面板。
 *
 * `always` 用于最后一条助手消息：卡内应用依赖模型主动输出占位符，模型漏写时
 * 消息正文里就不会出现面板；宿主在这里按该消息自己的 stat_data 快照补一份，
 * 保证「当前状态」总是可见（不改变历史消息的显示行为）。
 */
export const StatusPanelInline = ({
  panels,
  scripts,
  options,
  statData,
  author,
  authorMessages,
  sessionId,
  threadId,
  canWrite = false,
  canAct = false,
  actionMessageId,
  writeContext,
  streaming = false,
  netEnabled = false,
  always = false,
}: {
  panels?: StPanel[]
  scripts?: readonly RegexScript[]
  /** 未启用 HTML 渲染时传 undefined，直接不渲染 */
  options?: MarkdownRenderOptions
  /** 该条消息自己的 stat_data 快照 */
  statData?: Record<string, unknown>
  /** 酒馆助手作者脚本（TavernHelper）；有脚本时面板改走作者运行时 */
  author?: StAuthorPayload
  /** 作者运行时的消息镜像 */
  authorMessages?: AuthorRuntimeMessage[]
  /** 面板沙箱的网络代理需要会话 id 计账 */
  sessionId?: string
  /** 私聊窗口 id；作者脚本写入变量时回传 */
  threadId?: string
  /** 玩家名；作者脚本 context.name1 / 手机署名 */
  userName?: string
  /** 作者脚本是否可写回会话副本（st_card_write） */
  canWrite?: boolean
  /** 作者脚本是否可发送/继续/重生成（st_card_actions） */
  canAct?: boolean
  /** 继续/重生成的目标消息 id */
  actionMessageId?: string
  /** 世界书/角色副本读取快照 */
  writeContext?: CardWriteContext
  /** 当前会话是否正在生成 */
  streaming?: boolean
  /** 卡片网络代理开关；与聊天正文卡片保持一致 */
  netEnabled?: boolean
  /** 即使未开启「自动追加」也为该消息渲染面板（最后一条消息使用） */
  always?: boolean
}) => {
  const autoAppend = usePanelPrefs((state) => state.autoAppend)
  const hasAuthor = (author?.scripts?.length ?? 0) > 0
  if (
    !(autoAppend || always) ||
    !options?.htmlEnabled ||
    (!hasData(statData) && !hasAuthor) ||
    !panels?.length
  ) {
    return null
  }
  return (
    <div className={styles.inline}>
      <PanelBody
        panels={panels}
        statData={statData}
        options={options}
        scripts={scripts}
        author={author}
        authorMessages={authorMessages}
        sessionId={sessionId}
        threadId={threadId}
        canWrite={canWrite}
        canAct={canAct}
        actionMessageId={actionMessageId}
        writeContext={writeContext}
        streaming={streaming}
        netEnabled={netEnabled}
        runScripts
      />
    </div>
  )
}
