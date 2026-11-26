import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import {
  getGalleryPermission,
  isGallerySupported,
  listGalleryImages,
  openAppSettingsPage,
  readGalleryFile,
  requestGalleryPermission,
  type GalleryImage,
} from '../../services/pocGallery.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import styles from './GalleryPicker.module.css'

const PAGE_SIZE = 60
const PERMISSION_POLL_MS = 800
const PERMISSION_WAIT_MS = 30_000

/** 应用内相册选图：原生缩略图网格，多选后转成 File 加入输入框 */
export const GalleryPicker = ({
  onClose,
  onPick,
}: {
  onClose: () => void
  onPick: (files: File[]) => void
}) => {
  const t = useTranslate()
  const [supported] = useState(() => isGallerySupported())
  const [permission, setPermission] = useState(() => getGalleryPermission())
  const [images, setImages] = useState<GalleryImage[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const loadedRef = useRef(false)
  const pollRef = useRef(0)
  const imagesRef = useRef<GalleryImage[]>([])
  imagesRef.current = images

  const load = useCallback(
    async (offset: number, reset: boolean) => {
      if (!getGalleryPermission()) {
        setPermission(false)
        return
      }
      setPermission(true)
      loadedRef.current = true
      setLoading(true)
      setError('')
      // 桥调用是同步的（查询 + 生成缩略图），先让 loading 渲染一帧
      await new Promise((resolve) => window.setTimeout(resolve, 0))
      const result = listGalleryImages(offset, PAGE_SIZE)
      setLoading(false)
      if (!result) {
        setError(t('galleryLoadFailed'))
        return
      }
      setImages((prev) => (reset ? result.images : [...prev, ...result.images]))
      setHasMore(result.hasMore)
    },
    [t]
  )

  useEffect(() => {
    if (permission && !loadedRef.current) {
      void load(0, true)
    }
  }, [permission, load])

  // 从系统设置/权限弹窗返回后重新检查
  useEffect(() => {
    const check = () => {
      if (!getGalleryPermission()) {
        return
      }
      setPermission(true)
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
        pollRef.current = 0
      }
    }
    document.addEventListener('visibilitychange', check)
    window.addEventListener('focus', check)
    return () => {
      document.removeEventListener('visibilitychange', check)
      window.removeEventListener('focus', check)
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
        pollRef.current = 0
      }
    }
  }, [])

  const askPermission = () => {
    requestGalleryPermission()
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
    }
    const started = Date.now()
    pollRef.current = window.setInterval(() => {
      if (getGalleryPermission()) {
        window.clearInterval(pollRef.current)
        pollRef.current = 0
        setPermission(true)
      } else if (Date.now() - started > PERMISSION_WAIT_MS) {
        window.clearInterval(pollRef.current)
        pollRef.current = 0
      }
    }, PERMISSION_POLL_MS)
  }

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    )
  }

  const addSelected = async () => {
    if (busy || selected.length === 0) {
      return
    }
    setBusy(true)
    setError('')
    await new Promise((resolve) => window.setTimeout(resolve, 0))
    const files: File[] = []
    for (const id of selected) {
      const image = imagesRef.current.find((item) => item.id === id)
      if (!image) {
        continue
      }
      const file = readGalleryFile(image)
      if (!file) {
        setError(t('galleryReadFailed'))
        setBusy(false)
        return
      }
      files.push(file)
    }
    setBusy(false)
    if (files.length > 0) {
      onPick(files)
      onClose()
    }
  }

  if (!supported) {
    return null
  }

  const footer = (
    <div className={styles.footer}>
      <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
        {t('cancel')}
      </Button>
      <Button size="m" disabled={busy || selected.length === 0} onClick={() => void addSelected()}>
        {busy
          ? t('galleryReading')
          : t('galleryAdd', { count: selected.length })}
      </Button>
    </div>
  )

  return (
    <Modal
      title={t('galleryTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={footer}
    >
      {error ? (
        <div className={styles.error}>
          {error}
          {permission ? (
            <button
              type="button"
              className={styles.retry}
              onClick={() => void load(0, true)}
            >
              {t('uploadRetry', { message: t('galleryLoadFailed') })}
            </button>
          ) : null}
        </div>
      ) : null}
      {!permission ? (
        <div className={styles.permission}>
          <p className={styles.permissionText}>{t('galleryPermissionDesc')}</p>
          <div className={styles.permissionActions}>
            <Button size="m" onClick={askPermission}>
              {t('galleryGrant')}
            </Button>
            <Button size="m" variant="outlined" onClick={openAppSettingsPage}>
              {t('galleryOpenSettings')}
            </Button>
          </div>
        </div>
      ) : loading && images.length === 0 ? (
        <div className={styles.placeholder}>{t('galleryLoading')}</div>
      ) : images.length === 0 ? (
        <div className={styles.placeholder}>{t('galleryEmpty')}</div>
      ) : (
        <>
          <div className={styles.hint}>{t('gallerySelected', { count: selected.length })}</div>
          <div className={styles.grid}>
            {images.map((image) => {
              const index = selected.indexOf(image.id)
              return (
                <button
                  key={image.id}
                  type="button"
                  className={clsx(styles.item, index >= 0 && styles.itemSelected)}
                  title={image.name}
                  aria-pressed={index >= 0}
                  onClick={() => toggle(image.id)}
                >
                  {image.thumb ? (
                    <img src={image.thumb} alt={image.name} loading="lazy" />
                  ) : (
                    <span className={styles.broken} />
                  )}
                  {index >= 0 ? <span className={styles.badge}>{index + 1}</span> : null}
                </button>
              )
            })}
          </div>
          {hasMore ? (
            <div className={styles.more}>
              <Button
                size="s"
                variant="outlined"
                disabled={loading}
                onClick={() => void load(images.length, false)}
              >
                {loading ? t('galleryLoading') : t('galleryLoadMore')}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Modal>
  )
}
