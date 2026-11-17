import { useTranslate } from '../../i18n/index.js'
import { useRef, useState } from 'react'
import { assetUrl } from '../../services/flyai/client.js'
import { downloadUrl } from '../../services/native.js'
import type { ToolAudio } from '../../services/chat/view.js'
import { IconDownloadOutline16 } from '../base/icons.js'
import { showToast } from '../base/toastState.js'
import styles from './GeneratedAudio.module.css'

/** 语音下载文件名：data URL 按 MIME 取扩展名，站点资源取原文件名 */
const audioName = (src: string): string => {
  const dataMatch = /^data:audio\/([\w.+-]+)/i.exec(src)
  if (dataMatch) {
    const ext = dataMatch[1].toLowerCase().replace('mpeg', 'mp3')
    return `audio_${Date.now()}.${ext}`
  }
  try {
    const base = String(new URL(src, window.location.href).pathname).split('/').pop()
    if (base && /\.[A-Za-z0-9]{2,5}$/.test(base)) {
      return base
    }
  } catch {
    // 非法 URL 走兜底名
  }
  return 'audio.wav'
}

const fmtTime = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '00:00'
  }
  const total = Math.floor(seconds)
  const mm = String(Math.floor(total / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

const PlayIcon = () => (
  <svg width={16} height={16} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path
      d="M4.4 2.9c0-.65.7-1.05 1.27-.73l8.02 4.6a.85.85 0 0 1 0 1.46l-8.02 4.6A.85.85 0 0 1 4.4 13.1V2.9Z"
      fill="currentColor"
    />
  </svg>
)

const PauseIcon = () => (
  <svg width={16} height={16} viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="3.6" y="2.8" width="3.2" height="10.4" rx="1.1" fill="currentColor" />
    <rect x="9.2" y="2.8" width="3.2" height="10.4" rx="1.1" fill="currentColor" />
  </svg>
)

/** 单个语音条：播放/暂停、进度、时长与下载 */
const AudioPlayer = ({ audio }: { audio: ToolAudio }) => {
  const t = useTranslate()
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const src = assetUrl(audio.src)
  const speaker = audio.meta?.speaker?.trim()

  const toggle = () => {
    const element = ref.current
    if (!element) {
      return
    }
    if (element.paused) {
      void element.play().catch(() => showToast(t('audioPlayFailed')))
    } else {
      element.pause()
    }
  }

  const seek = (value: number) => {
    const element = ref.current
    if (element && Number.isFinite(value)) {
      element.currentTime = value
      setTime(value)
    }
  }

  return (
    <div className={styles.player}>
      <audio
        ref={ref}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          setTime(0)
        }}
        onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => {
          const value = event.currentTarget.duration
          setDuration(Number.isFinite(value) ? value : 0)
        }}
      />
      <button
        type="button"
        className={styles.play}
        aria-label={t(playing ? 'audioPause' : 'audioPlay')}
        onClick={toggle}
      >
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <div className={styles.body}>
        <div className={styles.meta}>
          <span className={styles.speaker}>{speaker || t('audioBubbleTitle')}</span>
          <span className={styles.time}>
            {fmtTime(time)} / {duration > 0 ? fmtTime(duration) : '--:--'}
          </span>
        </div>
        <input
          className={styles.progress}
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={Math.min(time, duration || 0)}
          aria-label={t('audioProgress')}
          onChange={(event) => seek(Number(event.target.value))}
        />
      </div>
      <button
        type="button"
        className={styles.download}
        aria-label={t('audioDownload')}
        title={t('audioDownload')}
        onClick={() => {
          // APK 内走 FlySaver 存到下载目录；Web 端走浏览器下载
          void downloadUrl(src, audioName(src)).then((ok) => {
            if (!ok) {
              showToast(t('audioDownloadFailed'))
            }
          })
        }}
      >
        <IconDownloadOutline16 />
      </button>
    </div>
  )
}

/** 助手消息内的 TTS 语音条；工具输出中的标记文本不再外显 */
export const GeneratedAudio = ({ audios }: { audios: ToolAudio[] }) => {
  if (!audios.length) {
    return null
  }
  return (
    <div className={styles.audios}>
      {audios.map((audio, index) => (
        <AudioPlayer key={`${audio.src}-${index}`} audio={audio} />
      ))}
    </div>
  )
}