import clsx from 'clsx'
import { useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { importConversationPayloads } from '../../services/chat/store.js'
import { FlyApiError } from '../../services/flyai/client.js'
import {
  MAX_DEEPSEEK_SESSIONS,
  type ParsedDeepSeekExport,
} from '../../services/flyai/deepseekExport.js'
import { formatDate } from '../../utils/format.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import styles from './DeepSeekImportDialog.module.css'

/**
 * DeepSeek 导出包的会话挑选：最多导入 MAX_DEEPSEEK_SESSIONS 个，
 * 导入后为普通私聊会话（无角色卡、无世界观）。
 */
export const DeepSeekImportDialog = ({
  parsed,
  onClose,
}: {
  parsed: ParsedDeepSeekExport
  onClose: () => void
}) => {
  const t = useTranslate()
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const atLimit = selected.length >= MAX_DEEPSEEK_SESSIONS
  const close = () => {
    if (!busy) {
      onClose()
    }
  }

  const toggle = (id: string, checked: boolean) => {
    if (checked && selected.length >= MAX_DEEPSEEK_SESSIONS) {
      showToast({ key: 'deepSeekImportLimit', params: { max: MAX_DEEPSEEK_SESSIONS }, type: 'error' })
      return
    }
    setSelected((prev) => (checked ? [...prev, id] : prev.filter((item) => item !== id)))
  }

  const runImport = async () => {
    if (busy || selected.length === 0) {
      return
    }
    const chosen = parsed.sessions.filter((session) => selected.includes(session.id))
    setBusy(true)
    setError(null)
    setProgress({ done: 0, total: chosen.length })
    try {
      const count = await importConversationPayloads(
        chosen.map((session) => session.payload),
        (done, total) => setProgress({ done, total })
      )
      showToast({ key: 'deepSeekImported', params: { count } })
      onClose()
    } catch (failure) {
      const message =
        failure instanceof FlyApiError
          ? failure.message
          : failure instanceof Error
            ? failure.message
            : t('unknownError')
      setError(message)
      showToast({ key: 'deepSeekImportFailed', params: { message }, type: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const percent = progress && progress.total > 0 ? (progress.done / progress.total) * 100 : 0

  return (
    <Modal
      title={t('deepSeekImportTitle')}
      className={styles.dialog}
      mainClassName={styles.main}
      closable={!busy}
      onClose={close}
      footer={
        <>
          <Button variant="outlined" size="m" disabled={busy} onClick={close}>
            {t('cancel')}
          </Button>
          <Button
            size="m"
            disabled={busy || selected.length === 0}
            onClick={() => void runImport()}
          >
            {busy && progress
              ? t('deepSeekImportBusy', { done: progress.done, total: progress.total })
              : t('deepSeekImportConfirm', { count: selected.length })}
          </Button>
        </>
      }
    >
      <p className={styles.hint}>
        {t('deepSeekImportHint', {
          count: parsed.sessions.length,
          max: MAX_DEEPSEEK_SESSIONS,
          selected: selected.length,
        })}
      </p>
      <div className={styles.list}>
        {parsed.sessions.map((session) => {
          const checked = selected.includes(session.id)
          const date = formatDate(session.updatedAt, '/')
          return (
            <label
              key={session.id}
              className={clsx(
                styles.row,
                checked && styles.rowChecked,
                !checked && atLimit && styles.rowMuted,
                busy && styles.rowDisabled
              )}
            >
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={checked}
                disabled={busy}
                onChange={(event) => toggle(session.id, event.target.checked)}
              />
              <span className={styles.rowMain}>
                <span className={styles.rowTitle}>{session.title}</span>
                <span className={styles.rowMeta}>
                  {date ? `${date} · ` : ''}
                  {t('deepSeekImportMessageCount', { count: session.messageCount })}
                </span>
              </span>
            </label>
          )
        })}
      </div>
      {busy && progress ? (
        <div className={styles.progress}>
          <div className={styles.progressBar} style={{ width: `${percent}%` }} />
        </div>
      ) : null}
      {error ? <div className={styles.error}>{t('deepSeekImportFailed', { message: error })}</div> : null}
    </Modal>
  )
}
