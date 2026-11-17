import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
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
        className={styles.stage}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            onClose()
          }
        }}
      >
        <img className={styles.img} src={current.src} alt={t('imageViewerAlt')} />
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