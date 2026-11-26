import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  backupToWebDav,
  deleteWebDavBackup,
  getConfig,
  getWebDavPending,
  listWebDavBackups,
  putConfig,
  restoreFromWebDav,
  testWebDav,
  type WebDavBackup,
  type WebDavSettingsView,
} from '../../../services/flyai/admin.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { showToast } from '../../base/toastState.js'
import {
  errorText,
  NumberField,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TextField,
} from './AdminCommon.js'
import styles from './AdminPanels.module.css'

/** WebDAV 云备份（仅管理员）：整库打包上传，恢复在重启后生效 */

type FormState = {
  enabled: boolean
  url: string
  username: string
  password: string
  remoteDir: string
  includeUploads: boolean
  keep: string
  verifyTls: boolean
  timeout: string
}

type Pending = {
  name?: string
  staged_at?: string
  files?: number
  bytes?: number
}

const toForm = (wd?: WebDavSettingsView): FormState => ({
  enabled: wd?.enabled ?? false,
  url: wd?.url ?? '',
  username: wd?.username ?? '',
  // 后端只回传掩码；原样回存时由后端还原旧密码
  password: wd?.password ?? '',
  remoteDir: wd?.remote_dir ?? 'FlyAI',
  includeUploads: wd?.include_uploads ?? false,
  keep: String(wd?.keep ?? 5),
  verifyTls: wd?.verify_tls ?? true,
  timeout: String(wd?.timeout_seconds ?? 60),
})

const parseIntIn = (value: string, min: number, max: number): number | undefined => {
  const parsed = Number(value.trim())
  return Number.isInteger(parsed) && parsed >= min && parsed <= max ? parsed : undefined
}

const formatBytes = (bytes: number): string => {
  if (!bytes) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

const formatTime = (value: string): string => {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export const WebDavPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [passwordSet, setPasswordSet] = useState(false)
  const [backups, setBackups] = useState<WebDavBackup[]>([])
  const [remoteDir, setRemoteDir] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const [confirming, setConfirming] = useState<{ type: 'restore' | 'delete'; name: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [backingUp, setBackingUp] = useState(false)
  const [busyName, setBusyName] = useState('')
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setForm(toForm(config.webdav))
      setPasswordSet(Boolean(config.webdav?.password_set))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  const loadPending = useCallback(async () => {
    try {
      const data = await getWebDavPending()
      setPending(data.pending)
    } catch {
      setPending(null)
    }
  }, [])

  const loadBackups = useCallback(async () => {
    setListError('')
    try {
      const data = await listWebDavBackups()
      setBackups(data.backups)
      setRemoteDir(data.remote_dir)
    } catch (err) {
      setBackups([])
      setListError(errorText(err))
    }
  }, [])

  useEffect(() => {
    void load()
    void loadPending()
  }, [load, loadPending])

  useEffect(() => {
    if (form?.enabled) {
      void loadBackups()
    }
  }, [form?.enabled, loadBackups])

  if (!isAdmin) {
    return <PanelEmpty text={t('systemNotAdmin')} />
  }

  const patch = (next: Partial<FormState>) =>
    setForm((current) => (current ? { ...current, ...next } : current))

  const save = async (): Promise<boolean> => {
    if (!form || saving) {
      return false
    }
    const keep = parseIntIn(form.keep, 1, 50)
    const timeout = parseIntIn(form.timeout, 5, 600)
    if (keep === undefined || timeout === undefined) {
      setError(t('webdavNumbersInvalid'))
      return false
    }
    if (form.enabled && !form.url.trim()) {
      setError(t('webdavUrlRequired'))
      return false
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({
        webdav: {
          enabled: form.enabled,
          url: form.url.trim(),
          username: form.username.trim(),
          password: form.password,
          remote_dir: form.remoteDir.trim(),
          include_uploads: form.includeUploads,
          keep,
          verify_tls: form.verifyTls,
          timeout_seconds: timeout,
        },
      })
      setForm(toForm(saved.webdav))
      setPasswordSet(Boolean(saved.webdav?.password_set))
      showToast({ key: 'webdavSaved' })
      if (saved.webdav?.enabled) {
        void loadBackups()
      }
      return true
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
      return false
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    if (testing) {
      return
    }
    // 先保存，保证测试的是刚填写的地址/账号
    if (!(await save())) {
      return
    }
    setTesting(true)
    try {
      const result = await testWebDav()
      if (result.ok) {
        showToast({ key: 'webdavTestOk' })
        void loadBackups()
      } else {
        setError(t('webdavTestFailed', { message: result.message }))
      }
    } catch (err) {
      setError(t('webdavTestFailed', { message: errorText(err) }))
    } finally {
      setTesting(false)
    }
  }

  const backupNow = async () => {
    if (backingUp) {
      return
    }
    setBackingUp(true)
    setError('')
    try {
      const result = await backupToWebDav()
      showToast({ key: 'webdavBackupDone', params: { name: result.name } })
      void loadBackups()
    } catch (err) {
      setError(t('webdavBackupFailed', { message: errorText(err) }))
    } finally {
      setBackingUp(false)
    }
  }

  const restore = async (name: string) => {
    if (busyName) {
      return
    }
    setConfirming(null)
    setBusyName(name)
    setError('')
    try {
      await restoreFromWebDav(name)
      await loadPending()
      showToast({ key: 'webdavRestoreStaged' })
    } catch (err) {
      setError(t('webdavRestoreFailed', { message: errorText(err) }))
    } finally {
      setBusyName('')
    }
  }

  const remove = async (name: string) => {
    if (busyName) {
      return
    }
    setConfirming(null)
    setBusyName(name)
    setError('')
    try {
      await deleteWebDavBackup(name)
      showToast({ key: 'webdavDeleted' })
      void loadBackups()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyName('')
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('webdavTitle')}</div>
          <div className={styles.subtitle}>{t('webdavSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="m" disabled={saving} onClick={() => void save()}>
            {t(saving ? 'adminSaving' : 'adminSave')}
          </Button>
          <Button size="m" disabled={testing || saving || !form} onClick={() => void test()}>
            {testing ? t('webdavTesting') : t('webdavTest')}
          </Button>
        </div>
      </div>

      {error ? <PanelError message={error} /> : null}
      {loading ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : !form ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : (
        <>
          <SwitchRow
            label={t('webdavEnabled')}
            desc={t('webdavEnabledDesc')}
            checked={form.enabled}
            onChange={(enabled) => patch({ enabled })}
          />

          <div className={styles.rows}>
            <TextField
              label={t('webdavUrl')}
              hint={t('webdavUrlHint')}
              value={form.url}
              placeholder="https://dav.jianguoyun.com/dav/"
              onChange={(value) => patch({ url: value })}
            />
            <TextField
              label={t('webdavUsername')}
              value={form.username}
              placeholder="name@example.com"
              onChange={(value) => patch({ username: value })}
            />
            <TextField
              label={t('webdavPassword')}
              hint={t(
                passwordSet ? 'webdavPasswordSetHint' : 'webdavPasswordUnsetHint'
              )}
              value={form.password}
              placeholder={t('webdavPasswordPlaceholder')}
              type="password"
              onChange={(value) => patch({ password: value })}
            />
            <TextField
              label={t('webdavRemoteDir')}
              hint={t('webdavRemoteDirHint')}
              value={form.remoteDir}
              placeholder="FlyAI"
              onChange={(value) => patch({ remoteDir: value })}
            />
            <NumberField
              label={t('webdavKeep')}
              hint={t('webdavKeepHint')}
              value={form.keep}
              placeholder="5"
              onChange={(value) => patch({ keep: value })}
            />
            <NumberField
              label={t('webdavTimeout')}
              hint={t('webdavTimeoutHint')}
              value={form.timeout}
              placeholder="60"
              onChange={(value) => patch({ timeout: value })}
            />
          </div>

          <SwitchRow
            label={t('webdavIncludeUploads')}
            desc={t('webdavIncludeUploadsDesc')}
            checked={form.includeUploads}
            onChange={(includeUploads) => patch({ includeUploads })}
          />
          <SwitchRow
            label={t('webdavVerifyTls')}
            desc={t('webdavVerifyTlsDesc')}
            checked={form.verifyTls}
            onChange={(verifyTls) => patch({ verifyTls })}
          />

          <div className={styles.hint}>{t('webdavSecretWarning')}</div>

          {pending ? (
            <div className={styles.card}>
              <div className={styles.cardHead}>
                <div className={styles.title}>{t('webdavPendingTitle')}</div>
              </div>
              <div className={styles.hint}>
                {t('webdavPendingDesc', {
                  name: pending.name ?? '',
                  time: formatTime(pending.staged_at ?? ''),
                })}
              </div>
            </div>
          ) : null}

          <div className={styles.sectionTitle}>{t('webdavBackupTitle')}</div>
          <div className={styles.actions}>
            <Button
              variant="outlined"
              size="m"
              disabled={backingUp || !form.enabled}
              onClick={() => void backupNow()}
            >
              {backingUp ? t('webdavBackingUp') : t('webdavBackupNow')}
            </Button>
            <Button
              variant="outlined"
              size="m"
              disabled={!form.enabled}
              onClick={() => void loadBackups()}
            >
              {t('webdavRefresh')}
            </Button>
          </div>
          {listError ? <PanelError message={listError} /> : null}
          {backups.length === 0 ? (
            <PanelEmpty text={form.enabled ? t('webdavNoBackups') : t('webdavEnableFirst')} />
          ) : (
            <div className={styles.list}>
              {backups.map((item) => (
                <div key={item.name} className={styles.row}>
                  <div className={styles.rowText}>
                    <div className={styles.rowLabel}>{item.name}</div>
                    <div className={styles.rowDesc}>
                      {formatBytes(item.size)}
                      {item.modified ? ` · ${formatTime(item.modified)}` : ''}
                      {remoteDir ? ` · ${remoteDir}/` : ''}
                    </div>
                  </div>
                  <div className={styles.actions}>
                    <Button
                      variant="outlined"
                      size="s"
                      disabled={Boolean(busyName)}
                      onClick={() => setConfirming({ type: 'restore', name: item.name })}
                    >
                      {busyName === item.name ? t('webdavRestoring') : t('webdavRestore')}
                    </Button>
                    <Button
                      variant="dangerOutlined"
                      size="s"
                      disabled={Boolean(busyName)}
                      onClick={() => setConfirming({ type: 'delete', name: item.name })}
                    >
                      {t('delete')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {confirming ? (
        <Modal
          title={t(
            confirming.type === 'restore'
              ? 'webdavRestoreTitle'
              : 'adminConfirmDeleteTitle'
          )}
          className={styles.dialog}
          onClose={() => (busyName ? undefined : setConfirming(null))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busyName)}
                onClick={() => setConfirming(null)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant={confirming.type === 'restore' ? 'primary' : 'danger'}
                size="m"
                disabled={Boolean(busyName)}
                onClick={() =>
                  void (confirming.type === 'restore'
                    ? restore(confirming.name)
                    : remove(confirming.name))
                }
              >
                {confirming.type === 'restore'
                  ? t('webdavRestore')
                  : t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t(
            confirming.type === 'restore'
              ? 'webdavRestoreConfirm'
              : 'webdavDeleteConfirm',
            { name: confirming.name }
          )}
        </Modal>
      ) : null}
    </div>
  )
}
