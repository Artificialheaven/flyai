import clsx from 'clsx'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { createPortal } from 'react-dom'
import { useTranslate } from '../../i18n/index.js'
import {
  IconCloseOutline16,
  IconCopyOutline16,
  IconDataOutline16,
  IconDownloadOutline16,
} from '../base/icons.js'
import { isTopOverlay, pushOverlay } from '../base/overlayStack.js'
import { showToast } from '../base/toastState.js'
import { assetUrl } from '../../services/flyai/client.js'
import { downloadUrl } from '../../services/native.js'
import type { GeneratedImageMeta } from '../../services/chat/markers.js'
import { copyText } from '../../utils/clipboard.js'
import styles from './ImageViewer.module.css'

export type ViewerImage = {
  src: string
  /** 生图工具产出的参数 JSON（SD / 云端）；用户上传图没有 */
  meta?: GeneratedImageMeta | null
}

/** 查看器手势：单指拖动 / 双指缩放 / 双击/滚轮缩放 */
type Point = { x: number; y: number }
type View = { scale: number; x: number; y: number }

const MIN_SCALE = 1
const MAX_SCALE = 6
const DOUBLE_TAP_SCALE = 2.5

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

const InfoBlock = ({ label, text, mono }: { label: string; text: string; mono?: boolean }) => {
  const t = useTranslate()
  return (
    <div className={styles.block}>
      <div className={styles.blockBar}>
        <span className={styles.blockLabel}>{label}</span>
        <button
          type="button"
          className={styles.copy}
          onClick={() => {
            void copyText(text).then((ok) => showToast(t(ok ? 'copied' : 'copyFailed')))
          }}
        >
          <IconCopyOutline16 />
          {t('copy')}
        </button>
      </div>
      <div className={clsx(styles.blockBody, mono && styles.mono)}>{text}</div>
    </div>
  )
}

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
  const t = useTranslate()
  if (meta.engine === 'sd') {
    const seedText = meta.seed === -1 || meta.seed === '-1' ? t('imageSeedRandom') : meta.seed
    return (
      <div className={styles.info}>
        <InfoHeader icon="🖥" title="Stable Diffusion" sub={meta.engine_label} />
        {meta.prompt ? <InfoBlock label={t('imagePositivePrompt')} text={meta.prompt} mono /> : null}
        {meta.negative_prompt ? (
          <InfoBlock label={t('imageNegativePrompt')} text={meta.negative_prompt} mono />
        ) : null}
        <InfoGrid
          rows={[
            [t('imageParamSize'), `${meta.width || '?'} × ${meta.height || '?'}`],
            [t('imageParamSteps'), meta.steps],
            ['CFG Scale', meta.cfg_scale],
            [t('imageParamSampler'), meta.sampler],
            [t('imageParamSchedule'), meta.schedule],
            [t('imageParamSeed'), seedText],
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
          title={t(isEdit ? 'imageCloudImg2img' : 'imageCloudTxt2img')}
          sub={meta.model ? t('imageModelSub', { model: meta.model }) : undefined}
        />
        {chips.length ? <InfoChips chips={chips} /> : null}
        {isEdit && meta.refs?.length ? (
          <div className={styles.block}>
            <div className={styles.blockBar}>
              <span className={styles.blockLabel}>
                {t('imageRefs', { count: meta.refs.length })}
              </span>
            </div>
            <div className={styles.refs}>
              {meta.refs.map((url, index) => (
                <button
                  key={`${url}-${index}`}
                  type="button"
                  className={styles.ref}
                  onClick={() => onOpenRef(url)}
                >
                  <img loading="lazy" src={assetUrl(url)} alt={t('imageRefAlt', { index: index + 1 })} />
                  <span className={styles.refCap}>
                    {t('imageRefAlt', { index: index + 1 })}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {meta.prompt ? <InfoBlock label={t('imagePrompt')} text={meta.prompt} /> : null}
      </div>
    )
  }
  return (
    <div className={styles.info}>
      <InfoHeader icon="🖼" title={t('imageInfoTitle')} />
      {meta.prompt ? <InfoBlock label={t('imagePrompt')} text={meta.prompt} /> : null}
    </div>
  )
}

/**
 * 全屏图片查看器（对照旧版 #img-viewer）：
 * 点背景关闭、Esc 先关信息面板再关查看器；面板按生图方式展示提示词与参数；
 * 参考图点击后在同一查看器内切换（新图无参数，面板随之关闭）。
 */
export const ImageViewer = ({ image, onClose }: { image: ViewerImage; onClose: () => void }) => {
  const t = useTranslate()
  const [current, setCurrent] = useState<ViewerImage>(() => ({
    src: assetUrl(image.src),
    meta: image.meta ?? null,
  }))
  const [panelOpen, setPanelOpen] = useState(false)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const panelOpenRef = useRef(panelOpen)
  panelOpenRef.current = panelOpen

  // ---- 手势状态 ----
  const stageRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [view, setView] = useState<View>({ scale: 1, x: 0, y: 0 })
  const viewRef = useRef(view)
  viewRef.current = view
  /** 当前按下的指针（多指缩放需要至少两个） */
  const pointersRef = useRef(new Map<number, Point>())
  const pinchRef = useRef<{
    distance: number
    scale: number
    center: Point
    x: number
    y: number
  } | null>(null)
  const dragRef = useRef<{ px: number; py: number; x: number; y: number } | null>(null)
  /** 拖动/缩放后抑制舞台的关闭点击 */
  const suppressClickRef = useRef(false)
  const suppressTimerRef = useRef(0)
  const lastTapRef = useRef<{ time: number; x: number; y: number } | null>(null)

  const markGesture = useCallback(() => {
    suppressClickRef.current = true
    window.clearTimeout(suppressTimerRef.current)
    suppressTimerRef.current = window.setTimeout(() => {
      suppressClickRef.current = false
    }, 400)
  }, [])

  /** 舞台内容区（去掉 padding）的尺寸与中心：手势坐标都以此为准 */
  const stageMetrics = useCallback(() => {
    const stage = stageRef.current
    if (!stage) {
      return null
    }
    const style = window.getComputedStyle(stage)
    const pl = parseFloat(style.paddingLeft) || 0
    const pr = parseFloat(style.paddingRight) || 0
    const pt = parseFloat(style.paddingTop) || 0
    const pb = parseFloat(style.paddingBottom) || 0
    const width = stage.clientWidth - pl - pr
    const height = stage.clientHeight - pt - pb
    return {
      centerX: pl + width / 2,
      centerY: pt + height / 2,
      width,
      height,
    }
  }, [])

  /** 限制缩放倍数，并把平移量约束在图片边界内 */
  const clampView = useCallback(
    (next: View): View => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next.scale))
      if (scale <= 1.001) {
        return { scale: 1, x: 0, y: 0 }
      }
      const img = imgRef.current
      const metrics = stageMetrics()
      if (!img || !metrics) {
        return { scale, x: next.x, y: next.y }
      }
      const maxX = Math.max(0, (img.offsetWidth * scale - metrics.width) / 2)
      const maxY = Math.max(0, (img.offsetHeight * scale - metrics.height) / 2)
      return {
        scale,
        x: Math.max(-maxX, Math.min(maxX, next.x)),
        y: Math.max(-maxY, Math.min(maxY, next.y)),
      }
    },
    [stageMetrics]
  )

  const stagePoint = useCallback((event: ReactPointerEvent<HTMLDivElement>): Point => {
    const rect = stageRef.current?.getBoundingClientRect()
    return {
      x: event.clientX - (rect?.left ?? 0),
      y: event.clientY - (rect?.top ?? 0),
    }
  }, [])

  const zoomAt = useCallback(
    (point: Point, nextScale: number) => {
      const current = viewRef.current
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, nextScale))
      if (scale <= 1.001) {
        setView({ scale: 1, x: 0, y: 0 })
        return
      }
      const metrics = stageMetrics()
      const cx = point.x - (metrics?.centerX ?? 0)
      const cy = point.y - (metrics?.centerY ?? 0)
      const ratio = scale / current.scale
      setView(
        clampView({
          scale,
          x: cx - ratio * (cx - current.x),
          y: cy - ratio * (cy - current.y),
        })
      )
    },
    [clampView, stageMetrics]
  )

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const point = stagePoint(event)
    pointersRef.current.set(event.pointerId, point)
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // 部分 WebView 不支持
    }
    if (pointersRef.current.size >= 2) {
      const [a, b] = [...pointersRef.current.values()]
      const metrics = stageMetrics()
      pinchRef.current = {
        distance: Math.hypot(b.x - a.x, b.y - a.y) || 1,
        scale: viewRef.current.scale,
        center: {
          x: (a.x + b.x) / 2 - (metrics?.centerX ?? 0),
          y: (a.y + b.y) / 2 - (metrics?.centerY ?? 0),
        },
        x: viewRef.current.x,
        y: viewRef.current.y,
      }
      dragRef.current = null
    } else {
      dragRef.current = {
        px: point.x,
        py: point.y,
        x: viewRef.current.x,
        y: viewRef.current.y,
      }
      suppressClickRef.current = false
    }
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointersRef.current.has(event.pointerId)) {
      return
    }
    const point = stagePoint(event)
    pointersRef.current.set(event.pointerId, point)

    if (pointersRef.current.size >= 2 && pinchRef.current) {
      const [a, b] = [...pointersRef.current.values()]
      const metrics = stageMetrics()
      if (!metrics) {
        return
      }
      const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1
      const pinch = pinchRef.current
      const scale = Math.min(
        MAX_SCALE,
        Math.max(MIN_SCALE, pinch.scale * (distance / pinch.distance))
      )
      const ratio = scale / pinch.scale
      const cx = (a.x + b.x) / 2 - metrics.centerX
      const cy = (a.y + b.y) / 2 - metrics.centerY
      setView(
        clampView({
          scale,
          x: cx - ratio * (pinch.center.x - pinch.x),
          y: cy - ratio * (pinch.center.y - pinch.y),
        })
      )
      markGesture()
      return
    }

    if (dragRef.current && pointersRef.current.size === 1) {
      if (viewRef.current.scale <= 1) {
        return
      }
      const dx = point.x - dragRef.current.px
      const dy = point.y - dragRef.current.py
      if (Math.abs(dx) + Math.abs(dy) > 3) {
        markGesture()
      }
      setView(
        clampView({
          scale: viewRef.current.scale,
          x: dragRef.current.x + dx,
          y: dragRef.current.y + dy,
        })
      )
    }
  }

  const onPointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const point = stagePoint(event)
    const hadPinch = pointersRef.current.size >= 2 || pinchRef.current !== null
    pointersRef.current.delete(event.pointerId)
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      // 忽略
    }
    if (pointersRef.current.size < 2) {
      pinchRef.current = null
    }
    if (pointersRef.current.size === 1) {
      // 双指松开一指：以剩余手指为新基准继续拖动
      const [rest] = [...pointersRef.current.values()]
      dragRef.current = {
        px: rest.x,
        py: rest.y,
        x: viewRef.current.x,
        y: viewRef.current.y,
      }
      return
    }
    dragRef.current = null

    // 双击缩放：两次轻点（未被拖动/缩放打断）且位置接近
    if (!hadPinch && !suppressClickRef.current) {
      const now = Date.now()
      const last = lastTapRef.current
      if (last && now - last.time < 320 && Math.hypot(point.x - last.x, point.y - last.y) < 30) {
        lastTapRef.current = null
        markGesture()
        zoomAt(point, viewRef.current.scale > 1.05 ? 1 : DOUBLE_TAP_SCALE)
        return
      }
      lastTapRef.current = { time: now, x: point.x, y: point.y }
    }
  }

  // 切换图片时复位视图
  useEffect(() => {
    setView({ scale: 1, x: 0, y: 0 })
  }, [current.src])

  // 窗口尺寸 / 参数面板变化后重新约束平移
  useEffect(() => {
    const onResize = () => setView((value) => clampView(value))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [clampView])

  useEffect(() => {
    const timer = window.setTimeout(() => setView((value) => clampView(value)), 320)
    return () => window.clearTimeout(timer)
  }, [panelOpen, clampView])

  // 桌面滚轮缩放（需非 passive 监听才能 preventDefault）
  useEffect(() => {
    const stage = stageRef.current
    if (!stage) {
      return
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = stage.getBoundingClientRect()
      const point = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      const nextScale = viewRef.current.scale * Math.exp(-event.deltaY * 0.0015)
      zoomAt(point, nextScale)
    }
    stage.addEventListener('wheel', onWheel, { passive: false })
    return () => stage.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const hasMeta = !!(
    current.meta &&
    (current.meta.prompt || current.meta.negative_prompt || current.meta.model || current.meta.sampler)
  )

  const openImage = useCallback((src: string) => {
    setCurrent({ src: assetUrl(src), meta: null })
    setPanelOpen(false)
  }, [])

  useEffect(() => {
    // 先关参数面板、再关查看器；Esc 与 Android 返回键行为一致
    const closeLayer = () => {
      if (panelOpenRef.current) {
        setPanelOpen(false)
      } else {
        onCloseRef.current()
      }
    }
    const pop = pushOverlay(closeLayer)
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isTopOverlay(closeLayer)) {
        event.preventDefault()
        closeLayer()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      pop()
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  const download = async () => {
    const src = current.src
    if (!src) {
      return
    }
    const name = suggestImageName(src)
    // APK 内交给 FlySaver 写入下载目录，Web 端走浏览器下载
    const ok = await downloadUrl(src, name)
    if (!ok) {
      showToast(t('imageDownloadFailed'))
    }
  }

  return createPortal(
    <div
      className={clsx(styles.viewer, panelOpen && styles.panelOpen)}
      role="dialog"
      aria-modal="true"
      aria-label={t('imageViewerLabel')}
    >
      <div
        ref={stageRef}
        className={styles.stage}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onClick={(event) => {
          if (suppressClickRef.current) {
            suppressClickRef.current = false
            return
          }
          if (event.target === event.currentTarget) {
            onClose()
          }
        }}
      >
        <img
          ref={imgRef}
          className={styles.img}
          src={current.src}
          alt={t('imageViewerAlt')}
          draggable={false}
          style={{
            transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`,
          }}
        />
      </div>

      <div className={styles.topbar}>
        {hasMeta ? (
          <button
            type="button"
            className={clsx(styles.btn, panelOpen && styles.btnActive)}
            title={t('imageInfo')}
            onClick={() => setPanelOpen((value) => !value)}
          >
            <IconDataOutline16 />
            <span className={styles.btnLabel}>{t('imageInfo')}</span>
          </button>
        ) : null}
        <button
          type="button"
          className={styles.btn}
          title={t('imageDownload')}
          onClick={() => void download()}
        >
          <IconDownloadOutline16 />
          <span className={styles.btnLabel}>{t('imageDownload')}</span>
        </button>
        <button type="button" className={styles.btn} title={t('close')} onClick={onClose}>
          <IconCloseOutline16 />
          <span className={styles.btnLabel}>{t('close')}</span>
        </button>
      </div>

      <div className={clsx(styles.panel, panelOpen && styles.panelVisible)}>
        {hasMeta && current.meta ? <InfoPanel meta={current.meta} onOpenRef={openImage} /> : null}
      </div>
    </div>,
    document.body
  )
}