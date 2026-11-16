import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { showToast } from '../../components/base/toastState.js'
import { assetUrl } from '../../services/api/client.js'
import { copyText } from '../../utils/clipboard.js'
import type { GeneratedImageMeta } from '../chat/messages/markers.js'
import styles from './ImageViewer.module.css'

export type ViewerImage = {
  src: string
  /** 生图工具产出的参数 JSON（SD / 云端）；用户上传图没有 */
  meta?: GeneratedImageMeta | null
}

/** data URL / 站点资源 → 下载文件名（对照旧版 suggestImageName） */
export const suggestImageName = (src: string): string => {
  const dataMatch = /^data:image\/([\w.+-]+)/i.exec(src)
  if (dataMatch) {
    const ext = dataMatch[1].toLowerCase().replace('jpeg', 'jpg')
    return `image_${viewerStamp()}.${ext}`
  }
  try {
    const base = String(new URL(src, window.location.href).pathname).split('/').pop()
    if (base && /\.[A-Za-z0-9]{2,5}$/.test(base)) {
      return base
    }
  } catch {
    // 非法 URL 走兜底名
  }
  return 'image.jpg'
}

const viewerStamp = (): string => {
  const d = new Date()
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}

const saveBlob = (blob: Blob, name: string) => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const InfoHeader = ({ icon, title, sub }: { icon: string; title: string; sub?: string }) => (
  <div className={styles.infoHead}>
    <span className={styles.infoIcon} aria-hidden>
      {icon}
    </span>
    <div>
      <div className={styles.infoTitle}>{title}</div>
      {sub ? <div className={styles.infoSub}>{sub}</div> : null}
    </div>
  </div>
)

const InfoBlock = ({ label, text, mono }: { label: string; text: string; mono?: boolean }) => (
  <div className={styles.block}>
    <div className={styles.blockBar}>
      <span className={styles.blockLabel}>{label}</span>
      <button
        type="button"
        className={styles.copy}
        onClick={() => {
          void copyText(text).then((ok) => showToast(ok ? '已复制到剪贴板' : '复制失败，请手动选择', !ok))
        }}
      >
        复制
      </button>
    </div>
    <div className={clsx(styles.blockBody, mono && styles.mono)}>{text}</div>
  </div>
)

const InfoGrid = ({ rows }: { rows: Array<[string, unknown]> }) => {
  const visible = rows.filter(([, value]) => value !== undefined && value !== null && value !== '')
  if (!visible.length) {
    return null
  }
  return (
    <div className={styles.paramGrid}>
      {visible.map(([key, value]) => (
        <div key={key} className={styles.param}>
          <span className={styles.paramK}>{key}</span>
          <span className={styles.paramV} title={String(value)}>
            {String(value)}
          </span>
        </div>
      ))}
    </div>
  )
}

const InfoChips = ({ chips }: { chips: string[] }) => (
  <div className={styles.chips}>
    {chips.map((chip) => (
      <span key={chip} className={styles.chip}>
        {chip}
      </span>
    ))}
  </div>
)

/** 信息面板：SD 与云端两种生图方式结构不同（对照旧版 buildImageInfoPanel） */
const InfoPanel = ({
  meta,
  onOpenRef,
}: {
  meta: GeneratedImageMeta
  onOpenRef: (url: string) => void
}) => {
  if (meta.engine === 'sd') {
    const seedText = meta.seed === -1 || meta.seed === '-1' ? '随机' : meta.seed
    return (
      <div className={styles.info}>
        <InfoHeader icon="🖥" title="Stable Diffusion" sub={meta.engine_label || 'Anything V5 · 文生图'} />
        {meta.prompt ? <InfoBlock label="正向提示词" text={meta.prompt} mono /> : null}
        {meta.negative_prompt ? <InfoBlock label="负向提示词" text={meta.negative_prompt} mono /> : null}
        <InfoGrid
          rows={[
            ['尺寸', `${meta.width || '?'} × ${meta.height || '?'}`],
            ['采样步数', meta.steps],
            ['CFG Scale', meta.cfg_scale],
            ['采样器', meta.sampler],
            ['调度器', meta.schedule],
            ['种子', seedText],
          ]}
        />
      </div>
    )
  }
  if (meta.engine === 'cloud') {
    const isEdit = meta.mode === 'img2img'
    const chips: string[] = []
    if (meta.model) {
      chips.push(meta.model)
    }
    if (meta.size) {
      chips.push(meta.size)
    }
    return (
      <div className={styles.info}>
        <InfoHeader
          icon="☁️"
          title={isEdit ? '云端图生图' : '云端生图'}
          sub={meta.model ? `模型 ${meta.model}` : undefined}
        />
        {chips.length ? <InfoChips chips={chips} /> : null}
        {isEdit && meta.refs?.length ? (
          <div className={styles.block}>
            <div className={styles.blockBar}>
              <span className={styles.blockLabel}>参考图片（{meta.refs.length}）· 点击放大</span>
            </div>
            <div className={styles.refs}>
              {meta.refs.map((url, index) => (
                <button
                  key={`${url}-${index}`}
                  type="button"
                  className={styles.ref}
                  onClick={() => onOpenRef(url)}
                >
                  <img loading="lazy" src={assetUrl(url)} alt={`图${index + 1}`} />
                  <span className={styles.refCap}>图{index + 1}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {meta.prompt ? <InfoBlock label="提示词" text={meta.prompt} /> : null}
      </div>
    )
  }
  return (
    <div className={styles.info}>
      <InfoHeader icon="🖼" title="图片信息" />
      {meta.prompt ? <InfoBlock label="提示词" text={meta.prompt} /> : null}
    </div>
  )
}

/**
 * 全屏图片查看器（对照旧版 #img-viewer）：
 * 点背景关闭、Esc 先关信息面板再关查看器；面板按生图方式展示提示词与参数；
 * 参考图点击后在同一查看器内切换（新图无参数，面板随之关闭）。
 */
export const ImageViewer = ({ image, onClose }: { image: ViewerImage; onClose: () => void }) => {
  const [current, setCurrent] = useState<ViewerImage>(() => ({ src: assetUrl(image.src), meta: image.meta ?? null }))
  const [panelOpen, setPanelOpen] = useState(false)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  const hasMeta = !!(
    current.meta &&
    (current.meta.prompt || current.meta.negative_prompt || current.meta.model || current.meta.sampler)
  )

  const openImage = useCallback((src: string) => {
    setCurrent({ src: assetUrl(src), meta: null })
    setPanelOpen(false)
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      event.preventDefault()
      if (panelOpen) {
        setPanelOpen(false)
      } else {
        onCloseRef.current()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [panelOpen])

  const download = async () => {
    const src = current.src
    if (!src) {
      return
    }
    const name = suggestImageName(src)
    try {
      const res = await fetch(src)
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      saveBlob(await res.blob(), name)
    } catch (error) {
      showToast(`保存图片失败: ${(error as Error).message}`, true)
    }
  }

  return createPortal(
    <div
      className={clsx(styles.viewer, panelOpen && styles.panelOpen)}
      role="dialog"
      aria-modal="true"
      aria-label="图片查看"
    >
      <div
        className={styles.stage}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            onClose()
          }
        }}
      >
        <img className={styles.img} src={current.src} alt="放大查看的图片" />
      </div>

      <div className={styles.topbar}>
        {hasMeta ? (
          <button
            type="button"
            className={clsx(styles.btn, panelOpen && styles.btnActive)}
            title="查看提示词"
            onClick={() => setPanelOpen((value) => !value)}
          >
            <span aria-hidden>📝</span>
            <span className={styles.btnLabel}>提示词</span>
          </button>
        ) : null}
        <button type="button" className={styles.btn} title="下载图片" onClick={() => void download()}>
          <span aria-hidden>⬇</span>
          <span className={styles.btnLabel}>下载</span>
        </button>
        <button type="button" className={styles.btn} title="关闭（Esc）" onClick={onClose}>
          <span aria-hidden>✕</span>
          <span className={styles.btnLabel}>关闭</span>
        </button>
      </div>

      <div className={clsx(styles.panel, panelOpen && styles.panelVisible)}>
        {hasMeta && current.meta ? <InfoPanel meta={current.meta} onOpenRef={openImage} /> : null}
      </div>
    </div>,
    document.body
  )
}