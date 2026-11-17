import { useEffect, useRef, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { formatSize } from '../../services/attachments.js'
import {
  downloadAppUpdate,
  installAppUpdate,
  onUpdateProgress,
  openUpdateInstallSettings,
  updateCanInstall,
  type UpdateProgress,
} from '../../services/native.js'
import {
  absoluteApkUrl,
  dismissAppUpdate,
  useAppUpdate,
  type AppUpdateInfo,
} from '../../services/flyai/appUpdate.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import styles from './UpdateDialog.module.css'

type Phase = 'idle' | 'downloading' | 'installing' | 'needPermission' | 'failed'

/** Capacitor 插件 reject 的错误带 code */
const errorCode = (error: unknown): string => {
  if (error && typeof error === 'object' && 'code' in error) {
    const code = (error as { code?: unknown }).code
    if (typeof code === 'string') {
      return code
    }
  }
  return ''
}

/**
 * APK 更新弹窗：授权检查 → 原生后台下载（进度条）→ 系统安装器。
 * 「稍后」关闭后本次会话不再提示；下载/安装期间禁止关闭，避免孤儿任务。
 */
export const UpdateDialog = () => {
  const t = useTranslate()
  const open = useAppUpdate((state) => state.open)
  const update = useAppUpdate((state) => state.update)
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<UpdateProgress>({ loaded: 0, total: 0, percent: 0 })
  /** 已下载完成的文件名：安装失败（如用户取消）后重试可直接安装，不必重下 */
  const [downloadedFile, setDownloadedFile] = useState<string | null>(null)
  const unsubscribeRef = useRef<(() => void) | null>(null)

  useEffect(
    () => () => {
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
    },
    []
  )

  const busy = phase === 'downloading' || phase === 'installing'

  const handleFailure = (error: unknown) => {
    const code = errorCode(error)
    if (code === 'UNKNOWN_SOURCES') {
      setPhase('needPermission')
      return
    }
    if (code === 'NOT_DOWNLOADED') {
      setDownloadedFile(null)
    }
    setPhase('failed')
  }

  const installDownloaded = async (fileName: string) => {
    setPhase('installing')
    try {
      await installAppUpdate(fileName)
    } catch (error) {
      handleFailure(error)
    }
  }

  const startDownload = async (info: AppUpdateInfo) => {
    unsubscribeRef.current?.()
    setPhase('downloading')
    setProgress({ loaded: 0, total: 0, percent: 0 })
    // 先订阅再下载：插件可能在 download() resolve 前就发进度
    unsubscribeRef.current = onUpdateProgress(setProgress)
    try {
      const result = await downloadAppUpdate(absoluteApkUrl(info.url), info.fileName)
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
      setDownloadedFile(result.fileName)
      await installDownloaded(result.fileName)
    } catch (error) {
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
      handleFailure(error)
    }
  }

  const run = async () => {
    if (!update || busy) {
      return
    }
    setPhase('idle')
    const check = await updateCanInstall()
    if (!check.allowed) {
      setPhase('needPermission')
      await openUpdateInstallSettings()
      return
    }
    if (downloadedFile) {
      await installDownloaded(downloadedFile)
      return
    }
    await startDownload(update)
  }

  if (!open || !update) {
    return null
  }

  const percent = Math.max(0, Math.min(100, Math.round(progress.percent)))
  const primaryLabel =
    phase === 'needPermission'
      ? t('appUpdateOpenSettings')
      : phase === 'downloading'
        ? t('appUpdateDownloading')
        : phase === 'installing'
          ? t('appUpdateInstalling')
          : t('appUpdateNow')

  return (
    <Modal
      title={t('appUpdateTitle')}
      closable={!busy}
      onClose={busy ? () => {} : dismissAppUpdate}
      footer={
        <>
          <Button variant="outlined" size="m" disabled={busy} onClick={dismissAppUpdate}>
            {t('appUpdateLater')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void run()}>
            {primaryLabel}
          </Button>
        </>
      }
    >
      <div className={styles.body}>
        <div className={styles.version}>
          {t('appUpdateVersionLine', {
            current: update.currentVersion,
            latest: update.version,
          })}
        </div>
        {update.size > 0 ? (
          <div className={styles.meta}>{t('appUpdateSizeLine', { size: formatSize(update.size) })}</div>
        ) : null}
        {phase === 'downloading' ? (
          <>
            <div
              className={styles.progress}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              aria-label={t('appUpdateDownloading')}
            >
              <div className={styles.progressBar} style={{ width: `${percent}%` }} />
            </div>
            <div className={styles.progressMeta}>
              <span>
                {progress.total > 0
                  ? `${formatSize(progress.loaded)} / ${formatSize(progress.total)}`
                  : t('appUpdateDownloadingUnknown')}
              </span>
              <span>{progress.total > 0 ? `${percent}%` : ''}</span>
            </div>
          </>
        ) : null}
        {phase === 'installing' ? (
          <div className={styles.hint}>{t('appUpdateInstalling')}</div>
        ) : null}
        {phase === 'needPermission' ? (
          <div className={styles.hint}>{t('appUpdateNeedPermission')}</div>
        ) : null}
        {phase === 'failed' ? <div className={styles.error}>{t('appUpdateFailed')}</div> : null}
      </div>
    </Modal>
  )
}
