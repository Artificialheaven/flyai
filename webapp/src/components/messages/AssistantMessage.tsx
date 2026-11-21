import { useTranslate } from '../../i18n/index.js'
import clsx from 'clsx'
import { memo, useMemo, useState } from 'react'
import { IconRefreshOutline16, IconWarningOutline16 } from '../base/icons.js'
import { describeReply } from '../../services/chat/describe.js'
import { canContinueReply } from '../../services/chat/reply.js'
import { continueReply, regenerateReply } from '../../services/chat/store.js'
import { stopReadAloud } from '../../services/speech/readAloud.js'
import type { AssistantMessage as AssistantMessageType } from '../../services/chat/types.js'
import type { RegexScript, StPanel } from '../../services/flyai/types.js'
import type { CardAppCapabilities, CardAppWriteContext } from '../../services/st/cardApp.js'
import { CardAppHostContext, type CardAppHost } from '../markdown/CardAppFrame.js'
import { openSettings } from '../settings/settingsDialogState.js'
import { IconButton } from '../base/IconButton.js'
import { Button } from '../base/Button.js'
import { Markdown } from '../markdown/Markdown.js'
import type { MarkdownRenderOptions } from '../markdown/SanitizedHtml.js'
import { SiteThumbnails } from '../search/SiteLogo.js'
import { toggleSearchPanel, useSearchPanel } from '../search/searchPanelState.js'
import { ReadAloudButton } from './ReadAloudButton.js'
import { CopyButton } from './CopyButton.js'
import { GeneratedAudio } from './GeneratedAudio.js'
import { GeneratedImages } from './GeneratedImages.js'
import { PixivWall } from './PixivWall.js'
import { buildPixivReveal } from '../../services/chat/markers.js'
import { RecallNotice } from './RecallNotice.js'
import { ReplyArea } from './ReplyArea.js'
import { StatusPanelInline } from '../session/StatusPanel.js'
import { useReplyView } from './useReplyView.js'
import { ImageViewer } from '../images/ImageViewer.js'
import type { ToolImage } from '../../services/chat/view.js'
import {
  BranchSwitcher,
  type MessageVersions,
  type SelectMessageVersion,
} from './BranchSwitcher.js'
import styles from './AssistantMessage.module.css'

export const AssistantMessage = memo(function AssistantMessage({
  sessionId,
  message,
  isLast,
  streaming,
  busy,
  showSpeaker,
  editable,
  versions,
  onSelectVersion,
  stScripts,
  stDepth,
  stHtml,
  stVariables,
  stPanels,
  cardFeatures,
  cardWriteContext,
  threadId,
}: {
  sessionId: string
  message: AssistantMessageType
  isLast: boolean
  streaming: boolean
  busy: boolean
  /** 群聊窗口：在每条回复上方标出发言者 */
  showSpeaker: boolean
  /** 群聊/只读窗口不支持重新生成与续写 */
  editable: boolean
  versions: MessageVersions
  onSelectVersion: SelectMessageVersion
  /** 酒馆展示侧正则（已按发言者角色卡选好）；只影响显示 */
  stScripts?: readonly RegexScript[]
  /** 消息距对话末尾的深度（脚本 minDepth/maxDepth 用） */
  stDepth?: number
  /** 酒馆卡 HTML/外链图片策略；未启用时保持转义文本 */
  stHtml?: MarkdownRenderOptions
  /** MVU 变量快照：展开展示侧的变量宏 */
  stVariables?: Record<string, unknown>
  /**
   * 卡内状态面板模板；开启「自动追加」时在每条消息下方渲染自身快照，
   * 最后一条消息即使未开启也会渲染（正文已内嵌面板时不重复）。
   */
  stPanels?: StPanel[]
  /** 用户设置层面的卡片能力开关；缺省全部关闭（只读） */
  cardFeatures?: CardAppCapabilities
  /** 会话副本写入读侧快照（世界书、角色白名单字段） */
  cardWriteContext?: CardAppWriteContext | null
  /** 当前私聊窗口 id；卡片写入变量时回传给后端 */
  threadId?: string
}) {
  const t = useTranslate()
  // 卡内能力随消息上下文下发：只有消息气泡提供宿主上下文，面板/抽屉保持只读
  const cardHost = useMemo<CardAppHost>(
    () => ({
      sessionId,
      messageId: message.id,
      isLast,
      editable,
      streaming: streaming || busy,
      threadId,
      features: cardFeatures ?? { actions: false, write: false, net: false },
      writeContext: cardWriteContext ?? null,
    }),
    [
      busy,
      cardFeatures,
      cardWriteContext,
      editable,
      isLast,
      message.id,
      sessionId,
      streaming,
      threadId,
    ]
  )
  const view = useReplyView(message, streaming)
  const panelEntry = useSearchPanel((state) => state.entry)
  const notice = streaming ? null : describeReply(message)
  // 安全拒绝：已下发的思考与正文按协议丢弃，只显示模板答复。
  const refused = message.stopReason === 'refusal'
  const answer = refused ? null : view.answer
  // 消息原文里已经带面板占位符时，展示侧正则会把它渲染在正文里；
  // 此时不再追加宿主面板，避免出现两份。模型漏写占位符时由最后一条消息兜底。
  const embeddedPanel = useMemo(
    () =>
      Boolean(
        stScripts?.length &&
          stPanels?.some(
            (panel) =>
              panel.token && (answer?.text || '').includes(panel.token)
          )
      ),
    [answer?.text, stPanels, stScripts]
  )
  const responseText = [answer?.text, notice?.presentation === 'response' ? notice.text : null]
    .filter(Boolean)
    .join('\n\n')
  const copyText = responseText || notice?.text
  const waiting = streaming && view.steps.length === 0 && !view.answer && !message.thinking
  const summaryEntry = `${message.id}-summary`
  const speaker = showSpeaker ? message.speakerName?.trim() : ''
  const recall = message.recall ?? []
  const images = view.steps.flatMap((step) => (step.type === 'tool' && step.image ? [step.image] : []))
  const audios = view.steps.flatMap((step) => (step.type === 'tool' && step.audio ? [step.audio] : []))
  const pixivWalls = view.steps.flatMap((step) =>
    step.type === 'tool' && step.pixiv ? [{ key: step.key, candidates: step.pixiv }] : []
  )
  // 选中的原图按渲染顺序回填到最近一次 search_pixiv 的候选墙
  const pixivReveal = buildPixivReveal(
    view.steps.flatMap((step) =>
      step.type === 'tool'
        ? [{ id: step.key, name: step.name, content: step.preview }]
        : []
    )
  )
  const [viewerImage, setViewerImage] = useState<ToolImage | null>(null)
  return (
    <CardAppHostContext.Provider value={cardHost}>
    <div className={styles.message}>
      {message.origin === 'card' ? (
        <div className={styles.originBadge}>{t('messageFromCard')}</div>
      ) : null}
      {speaker ? (
        <div className={styles.speaker}>
          <span className={styles.speakerAvatar}>{Array.from(speaker)[0]}</span>
          <span>{speaker}</span>
        </div>
      ) : null}
      {recall.length > 0 ? <RecallNotice items={recall} /> : null}
      {refused ? null : (
        <ReplyArea
          message={message}
          view={view}
          streaming={streaming}
          hasResponse={!!responseText}
          stScripts={stScripts}
          stDepth={stDepth}
          stHtml={stHtml}
          stVariables={stVariables}
        />
      )}
      {answer ? (
        <Markdown
          className="ds-assistant-message-main-content"
          content={answer.markdown}
          loading={streaming}
          cacheKey={message.id}
          disableLink={false}
          sources={view.sources}
          stScripts={stScripts}
          stDepth={stDepth}
          stHtml={stHtml}
          stVariables={stVariables}
        />
      ) : null}
      {refused ? null : <GeneratedImages images={images} onOpen={setViewerImage} />}
      {refused ? null : <GeneratedAudio audios={audios} />}
      {refused
        ? null
        : pixivWalls.map((wall) => (
            <PixivWall
              key={wall.key}
              candidates={wall.candidates}
              reveal={pixivReveal[wall.key]}
              onOpen={setViewerImage}
            />
          ))}
      {!streaming && !refused && stPanels?.length && !embeddedPanel ? (
        <StatusPanelInline
          panels={stPanels}
          scripts={stScripts}
          options={stHtml}
          statData={message.statData}
          sessionId={sessionId}
          netEnabled={cardFeatures?.net === true}
          always={isLast}
        />
      ) : null}
      {waiting ? (
        <div className={styles.waiting}>
          {[0, 1, 2].map((index) => (
            <span key={index} className={styles.waitingDot} />
          ))}
        </div>
      ) : null}
      {notice?.presentation === 'response' ? (
        <Markdown
          className="ds-assistant-message-main-content"
          content={notice.text}
          loading={false}
          cacheKey={`${message.id}-template`}
          disableLink={false}
        />
      ) : notice ? (
        <div className={styles.notice}>
          <IconWarningOutline16 />
          <span>
            {notice.text}
            {notice.action === 'topUp' ? (
              <>
                {' '}
                <button
                  type="button"
                  className={styles.noticeAction}
                  onClick={() => openSettings('data')}
                >
                  {t('topUp')}
                </button>
              </>
            ) : null}
          </span>
        </div>
      ) : null}
      {!streaming && !refused && view.searchResults.length > 0 ? (
        <div
          className={clsx(styles.summary, panelEntry === summaryEntry && styles.summaryActive)}
          onClick={() => toggleSearchPanel(summaryEntry, view.searchResults)}
        >
          <SiteThumbnails sources={view.searchResults} />
          <span>
            {t(
              view.searchResults.length === 1 ? 'messageSearchSummary' : 'messageSearchSummaries',
              { count: view.searchResults.length }
            )}
          </span>
        </div>
      ) : null}
      {streaming ? null : (
        <div className={styles.bottom}>
          <BranchSwitcher versions={versions} disabled={busy} onSelect={onSelectVersion} />
          <div className={clsx(styles.actions, isLast && styles.alwaysShow)}>
            {copyText ? <CopyButton text={copyText} /> : null}
            {responseText && !refused ? (
              <ReadAloudButton messageId={message.id} text={responseText} />
            ) : null}
            {editable ? (
              <IconButton
                icon={<IconRefreshOutline16 />}
                tooltip={t('regenerate')}
                tooltipPlacement="bottom"
                disabled={busy || refused}
                onClick={() => {
                  stopReadAloud()
                  regenerateReply(sessionId, message.id)
                }}
              />
            ) : null}
          </div>
          {editable && isLast && canContinueReply(message) ? (
            <Button
              variant="outlined"
              size="s"
              className={styles.continueButton}
              disabled={busy}
              onClick={() => {
                stopReadAloud()
                continueReply(sessionId, message.id)
              }}
            >
              {t('continueGenerating')}
            </Button>
          ) : null}
        </div>
      )}
      {viewerImage ? (
        <ImageViewer image={viewerImage} onClose={() => setViewerImage(null)} />
      ) : null}
    </div>
    </CardAppHostContext.Provider>
  )
})
