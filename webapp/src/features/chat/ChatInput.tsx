import clsx from 'clsx'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import {
  IconPaperclipOutline16,
  IconSendOutline16,
  IconStopFill16,
  IconThinkOutline16,
} from '../../components/base/icons.js'
import { IMAGE_MAX_COUNT, imageToDataUrl } from '../../utils/image.js'
import { useChatStore } from '../../stores/chatStore.js'
import { useConfigStore } from '../../stores/configStore.js'
import type { MentionMember } from '../../services/chat/mentions.js'
import { VoiceButton } from './VoiceButton.js'
import { useVoiceInput } from './useVoiceInput.js'
import styles from './ChatInput.module.css'

const MAX_TEXTAREA_HEIGHT = 200

/** 取光标前最后一个 @ 提及片段；返回候选过滤词与替换区间 */
const matchMention = (
  text: string,
  cursor: number
): { keyword: string; start: number } | null => {
  const before = text.slice(0, cursor)
  const match = /(?:^|\s)@([^\s@]*)$/.exec(before)
  if (!match) {
    return null
  }
  const keyword = match[1]
  return { keyword, start: cursor - keyword.length - 1 }
}

export const ChatInput = ({ mentionMembers }: { mentionMembers: MentionMember[] }) => {
  const draft = useChatStore((state) => state.draft)
  const setDraft = useChatStore((state) => state.setDraft)
  const send = useChatStore((state) => state.send)
  const stop = useChatStore((state) => state.stop)
  const busy = useChatStore((state) => state.busy)
  const streamStatus = useChatStore((state) => state.streamStatus)
  const pendingImages = useChatStore((state) => state.pendingImages)
  const addImageFiles = useChatStore((state) => state.addImageFiles)
  const removeImage = useChatStore((state) => state.removeImage)
  const deepThinking = useChatStore((state) => state.deepThinking)
  const toggleDeepThinking = useChatStore((state) => state.toggleDeepThinking)
  const sdEngine = useChatStore((state) => state.sdEngine)
  const setSdEngine = useChatStore((state) => state.setSdEngine)
  const toggleSdEngine = useChatStore((state) => state.toggleSdEngine)
  const configLoaded = useConfigStore((state) => state.loaded)
  const sdAvailable = useConfigStore((state) => state.config?.stable_diffusion?.enabled === true)
  const ensureConfig = useConfigStore((state) => state.ensureLoaded)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)
  const [mentionOpen, setMentionOpen] = useState(false)

  const autosize = useCallback(() => {
    const el = textareaRef.current
    if (!el) {
      return
    }
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }, [])

  useLayoutEffect(() => {
    autosize()
  }, [draft, autosize])

  const insertAtCursor = useCallback(
    (snippet: string) => {
      const el = textareaRef.current
      if (!el) {
        setDraft(draft + snippet)
        return
      }
      const start = el.selectionStart ?? draft.length
      const end = el.selectionEnd ?? draft.length
      const next = draft.slice(0, start) + snippet + draft.slice(end)
      setDraft(next)
      requestAnimationFrame(() => {
        el.focus()
        const pos = start + snippet.length
        el.setSelectionRange(pos, pos)
      })
    },
    [draft, setDraft]
  )

  const voice = useVoiceInput((text) => insertAtCursor(draft ? `${draft}${draft.endsWith(' ') ? '' : ' '}${text} ` : `${text} `))

  // 配置决定 SD 入口是否可见；管理员关掉引擎后不能保留选中状态
  useEffect(() => {
    void ensureConfig()
  }, [ensureConfig])

  useEffect(() => {
    if (configLoaded && !sdAvailable && sdEngine) {
      setSdEngine(false)
    }
  }, [configLoaded, sdAvailable, sdEngine, setSdEngine])

  const mentionState = useMemo(() => {
    const el = textareaRef.current
    if (!el || mentionMembers.length === 0) {
      return null
    }
    return matchMention(draft, el.selectionStart ?? draft.length)
  }, [draft, mentionMembers])

  useEffect(() => {
    setMentionOpen(!!mentionState)
  }, [mentionState])

  const mentionCandidates = useMemo(() => {
    if (!mentionState) {
      return []
    }
    const keyword = mentionState.keyword.toLowerCase()
    const names = new Map<string, MentionMember>()
    for (const member of mentionMembers) {
      for (const name of [member.name, ...(member.aliases || [])]) {
        if (!names.has(name)) {
          names.set(name, member)
        }
      }
    }
    return [...names.entries()]
      .filter(([name]) => name.toLowerCase().includes(keyword))
      .slice(0, 8)
  }, [mentionState, mentionMembers])

  const applyMention = (name: string) => {
    const el = textareaRef.current
    if (!el || !mentionState) {
      return
    }
    const next = `${draft.slice(0, mentionState.start)}@${name} ${draft.slice(el.selectionStart ?? draft.length)}`
    setDraft(next)
    setMentionOpen(false)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !composingRef.current) {
      event.preventDefault()
      if (!busy) {
        void send()
      }
    }
  }

  const canSend = !busy && (!!draft.trim() || pendingImages.length > 0)
  const imageRoom = IMAGE_MAX_COUNT - pendingImages.length

  return (
    <div className={styles.wrap}>
      {mentionOpen && mentionCandidates.length > 0 ? (
        <div className={styles.mentionBox} role="listbox">
          {mentionCandidates.map(([name, member]) => (
            <button
              key={`${member.id}-${name}`}
              type="button"
              role="option"
              className={styles.mentionOption}
              onMouseDown={(event) => {
                event.preventDefault()
                applyMention(name)
              }}
            >
              @{name}
            </button>
          ))}
        </div>
      ) : null}

      {pendingImages.length > 0 ? (
        <div className={styles.attachRow}>
          {pendingImages.map((image, index) => (
            <div key={index} className={styles.attachCell}>
              <img src={imageToDataUrl(image)} alt="" />
              <button
                type="button"
                className={styles.attachRemove}
                aria-label="移除图片"
                onClick={() => removeImage(index)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className={styles.box}>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(event) => {
            const files = event.target.files ? Array.from(event.target.files) : []
            if (files.length) {
              void addImageFiles(files.slice(0, Math.max(0, imageRoom)))
            }
            event.target.value = ''
          }}
        />
        <button
          type="button"
          className={styles.iconBtn}
          title="附加图片"
          disabled={imageRoom <= 0}
          onClick={() => fileRef.current?.click()}
        >
          <IconPaperclipOutline16 />
        </button>

        {configLoaded && sdAvailable ? (
          <button
            type="button"
            className={clsx(styles.iconBtn, styles.sdBtn, sdEngine && styles.iconBtnActive)}
            title="使用本地 Stable Diffusion 生图（Anything V5）"
            aria-pressed={sdEngine}
            onClick={toggleSdEngine}
          >
            SD
          </button>
        ) : null}

        <textarea
          ref={textareaRef}
          className={styles.textarea}
          rows={1}
          placeholder={busy ? 'AI 正在回复…' : '输入消息，Enter 发送，Shift+Enter 换行'}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => {
            composingRef.current = true
          }}
          onCompositionEnd={() => {
            composingRef.current = false
          }}
        />

        <button
          type="button"
          className={clsx(styles.iconBtn, deepThinking && styles.iconBtnActive)}
          title="深度思考"
          aria-pressed={deepThinking}
          onClick={toggleDeepThinking}
        >
          <IconThinkOutline16 />
        </button>

        <VoiceButton
          status={voice.status}
          seconds={voice.seconds}
          disabled={!voice.available || busy}
          onClick={() => void voice.toggle()}
        />

        {busy ? (
          <button
            type="button"
            className={styles.stopBtn}
            title="停止生成"
            onClick={stop}
          >
            <IconStopFill16 />
          </button>
        ) : (
          <button
            type="button"
            className={styles.sendBtn}
            title="发送"
            disabled={!canSend}
            onClick={() => void send()}
          >
            <IconSendOutline16 />
          </button>
        )}
      </div>

      {streamStatus === 'reconnecting' ? (
        <div className={styles.statusHint}>连接中断，正在自动重连…</div>
      ) : null}
    </div>
  )
}
