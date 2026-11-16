import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { showToast } from '../../../components/base/toastState.js'
import { assetUrl } from '../../../services/api/client.js'
import type { GeneratedAudioMeta } from './markers.js'
import styles from './AudioCard.module.css'

/** 页面级互斥播放：任意语音条开始播放时暂停上一条 */
let activePause: (() => void) | null = null

export const formatAudioTime = (sec: number): string => {
  if (!Number.isFinite(sec) || sec < 0) {
    return '00:00'
  }
  const total = Math.round(sec)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

/** TTS 工具卡内的独立语音条（对照旧版 makeAudioCard） */
export const AudioCard = ({ src, meta }: { src: string; meta: GeneratedAudioMeta | null }) => {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)
  const [failed, setFailed] = useState(false)
  const url = assetUrl(src)

  useEffect(
    () => () => {
      const audio = audioRef.current
      if (audio) {
        audio.pause()
      }
      if (activePause) {
        activePause = null
      }
    },
    []
  )

  const toggle = () => {
    const audio = audioRef.current
    if (!audio || failed) {
      return
    }
    if (audio.paused) {
      activePause?.()
      const promise = audio.play()
      if (promise && promise.catch) {
        promise.catch(() => {
          setPlaying(false)
          showToast('当前浏览器不允许自动播放，请再点一次')
        })
      }
    } else {
      audio.pause()
    }
  }

  const seek = (event: MouseEvent<HTMLDivElement>) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0) {
      return
    }
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    audio.currentTime = ratio * audio.duration
    setCurrent(audio.currentTime)
  }

  const downloadName = (url.split('/').pop() || 'tts.mp3').split('?')[0] || 'tts.mp3'
  const percent = duration > 0 ? Math.min(100, (current / duration) * 100) : 0

  return (
    <div
      className={[styles.card, playing ? styles.playing : '', failed ? styles.err : ''].filter(Boolean).join(' ')}
      title={meta?.speaker ? `AI 语音 · ${meta.speaker}` : undefined}
    >
      <button
        type="button"
        className={styles.play}
        aria-label={playing ? '暂停' : '播放'}
        disabled={failed}
        onClick={toggle}
      >
        {playing ? '⏸' : '▶'}
      </button>
      <span className={styles.ico} aria-hidden>
        🔈
      </span>
      <div className={styles.bar} role="presentation" onClick={seek}>
        <div className={styles.played} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.time}>{failed ? '音频加载失败' : `${formatAudioTime(current)} / ${formatAudioTime(duration)}`}</span>
      <a className={styles.dl} href={url} download={downloadName} title="下载">
        ⤓
      </a>
      <audio
        ref={audioRef}
        hidden
        preload="metadata"
        src={url}
        onPlay={() => {
          activePause = () => audioRef.current?.pause()
          setPlaying(true)
        }}
        onPause={() => setPlaying(false)}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
        onDurationChange={(event) => setDuration(event.currentTarget.duration)}
        onTimeUpdate={(event) => {
          const audio = event.currentTarget
          setCurrent(audio.currentTime)
          if (Number.isFinite(audio.duration)) {
            setDuration(audio.duration)
          }
        }}
        onEnded={(event) => {
          setPlaying(false)
          setCurrent(0)
          event.currentTarget.currentTime = 0
        }}
        onError={() => setFailed(true)}
      />
    </div>
  )
}