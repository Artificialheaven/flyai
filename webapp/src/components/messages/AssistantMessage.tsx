import { useTranslate } from '../../i18n/index.js'
import clsx from 'clsx'
import { memo, useState } from 'react'
import { IconRefreshOutline16, IconWarningOutline16 } from '../base/icons.js'
import { describeReply } from '../../services/chat/describe.js'
import { canContinueReply } from '../../services/chat/reply.js'
import { continueReply, regenerateReply } from '../../services/chat/store.js'
import { stopReadAloud } from '../../services/speech/readAloud.js'
import type { AssistantMessage as AssistantMessageType } from '../../services/chat/types.js'
import { openSettings } from '../settings/settingsDialogState.js'
import { IconButton } from '../base/IconButton.js'
import { Button } from '../base/Button.js'
import { Markdown } from '../markdown/Markdown.js'
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
}) {
  const t = useTranslate()
  const view = useReplyView(message, streaming)
  const panelEntry = useSearchPanel((state) => state.entry)
  const notice = streaming ? null : describeReply(message)
  // 安全拒绝：已下发的思考与正文按协议丢弃，只显示模板答复。
  const refused = message.stopReason === 'refusal'
  const answer = refused ? null : view.answer
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
    <div className={styles.message}>
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
  )
})
