import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  createUser,
  deleteUser,
  listUsers,
  resetUserPassword,
  updateUser,
  type AdminUser,
} from '../../../services/flyai/admin.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { showToast } from '../../base/toastState.js'
import { errorText, NumberField, PanelEmpty, PanelError } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

const formatNumber = (value: number): string =>
  (Math.round(value * 10) / 10).toLocaleString()

const quotaText = (user: AdminUser): string => {
  const quota = user.quota
  if (!quota) {
    return '—'
  }
  if (quota.total === -1) {
    return '∞'
  }
  const remaining = quota.remaining ?? Math.max(0, quota.total - quota.used)
  return `${formatNumber(remaining)} / ${formatNumber(quota.total)}`
}

/** 用户管理（仅管理员）：列表、创建、改等级/额度、重置密码、删除 */
export const UsersPanel = () => {
  const t = useTranslate()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [resetting, setResetting] = useState<AdminUser | null>(null)
  const [deleting, setDeleting] = useState<AdminUser | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setUsers(await listUsers())
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const confirmDelete = async () => {
    if (!deleting || busyId) {
      return
    }
    setBusyId(deleting.id)
    setError('')
    try {
      await deleteUser(deleting.id)
      setDeleting(null)
      showToast({ key: 'adminDeleted' })
      await load()
    } catch (err) {
      setError(t('adminDeleteFailed', { message: errorText(err) }))
    } finally {
      setBusyId('')
    }
  }

  const initial = (name: string): string => (name.trim()[0] ?? '?').toUpperCase()

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideUsersOptionLabel')}</div>
          <div className={styles.subtitle}>{t('usersSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button size="s" onClick={() => setCreating(true)}>
            {t('usersCreate')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}
      {loading ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : users.length === 0 ? (
        <PanelEmpty text={t('usersEmpty')} />
      ) : (
        <div className={styles.list}>
          {users.map((user) => (
            <div key={user.id} className={styles.item}>
              <div className={styles.avatar}>{initial(user.name)}</div>
              <div className={styles.itemMain}>
                <div className={styles.itemName}>
                  <span>{user.name}</span>
                  <span className={styles.badge}>
                    {t('usersLevel', { level: user.level })}
                  </span>
                </div>
                <div className={styles.itemMeta}>
                  {t('usersQuotaLabel', { quota: quotaText(user) })}
                  {user.quota && user.quota.total !== -1
                    ? ` · ${t('usersQuotaUsed', { used: formatNumber(user.quota.used) })}`
                    : ''}
                </div>
              </div>
              <div className={styles.itemActions}>
                <Button
                  size="s"
                  variant="outlined"
                  disabled={busyId === user.id}
                  onClick={() => setEditing(user)}
                >
                  {t('adminEdit')}
                </Button>
                <Button
                  size="s"
                  variant="outlined"
                  disabled={busyId === user.id}
                  onClick={() => setResetting(user)}
                >
                  {t('usersResetPassword')}
                </Button>
                <Button
                  size="s"
                  variant="dangerOutlined"
                  disabled={busyId === user.id}
                  onClick={() => setDeleting(user)}
                >
                  {t('adminDelete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {creating ? (
        <UserForm
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false)
            showToast({ key: 'usersCreated' })
            void load()
          }}
        />
      ) : null}
      {editing ? (
        <UserForm
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            showToast({ key: 'usersUpdated' })
            void load()
          }}
        />
      ) : null}
      {resetting ? (
        <PasswordForm
          user={resetting}
          onClose={() => setResetting(null)}
          onSaved={() => {
            setResetting(null)
            showToast({ key: 'usersPasswordUpdated' })
          }}
        />
      ) : null}
      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => (busyId ? undefined : setDeleting(null))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busyId)}
                onClick={() => setDeleting(null)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                size="m"
                disabled={Boolean(busyId)}
                onClick={() => void confirmDelete()}
              >
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('usersDeleteDesc', { name: deleting.name })}
        </Modal>
      ) : null}
    </div>
  )
}

/** 创建 / 编辑用户：创建时含用户名与密码；编辑只改等级与额度 */
const UserForm = ({
  user,
  onClose,
  onSaved,
}: {
  user?: AdminUser
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const isCreate = !user
  const [name, setName] = useState(user?.name ?? '')
  const [password, setPassword] = useState('')
  const [level, setLevel] = useState(String(user?.level ?? 0))
  const initialQuota = user?.quota ? user.quota.total : 0
  const [quota, setQuota] = useState(String(initialQuota))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (busy) {
      return
    }
    if (isCreate && !name.trim()) {
      setError(t('usersNameRequired'))
      return
    }
    const levelValue = Number(level)
    if (!Number.isInteger(levelValue) || levelValue < 0 || levelValue > 255) {
      setError(t('usersLevelInvalid'))
      return
    }
    const quotaValue = Number(quota)
    if (!Number.isFinite(quotaValue) || quotaValue < -1) {
      setError(t('usersQuotaInvalid'))
      return
    }
    setBusy(true)
    setError('')
    try {
      if (user) {
        await updateUser(user.id, { level: levelValue, quota: quotaValue })
      } else {
        await createUser({
          name: name.trim(),
          password,
          level: levelValue,
          quota: quotaValue,
        })
      }
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t(isCreate ? 'usersCreateTitle' : 'usersEditTitle', { name: user?.name ?? '' })}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void submit()}>
            {t(busy ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      }
    >
      {error ? <PanelError message={error} /> : null}
      {isCreate ? (
        <>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('usersFieldName')}</span>
            <input
              className={styles.input}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('usersFieldPassword')}</span>
            <input
              className={styles.input}
              type="password"
              value={password}
              placeholder={t('usersFieldPasswordHint')}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
        </>
      ) : null}
      <NumberField
        label={t('usersFieldLevel')}
        hint={t('usersFieldLevelHint')}
        value={level}
        onChange={setLevel}
      />
      <NumberField
        label={t('usersFieldQuota')}
        hint={t('usersFieldQuotaHint')}
        value={quota}
        onChange={setQuota}
      />
      {user?.quota && user.quota.total !== -1 ? (
        <div className={styles.quotaUser}>
          <span>
            {t('usersQuotaUsed', { used: formatNumber(user.quota.used) })}
          </span>
          <span>{t('usersQuotaTotal', { total: formatNumber(user.quota.total) })}</span>
        </div>
      ) : null}
    </Modal>
  )
}

/** 重置密码：成功后该用户所有已登录令牌都会被吊销 */
const PasswordForm = ({
  user,
  onClose,
  onSaved,
}: {
  user: AdminUser
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (busy) {
      return
    }
    if (!password.trim()) {
      setError(t('usersPasswordRequired'))
      return
    }
    setBusy(true)
    setError('')
    try {
      await resetUserPassword(user.id, password)
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t('usersResetPasswordTitle', { name: user.name })}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        <div className={styles.dialogFooter}>
          <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button size="m" disabled={busy} onClick={() => void submit()}>
            {t(busy ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      }
    >
      {error ? <PanelError message={error} /> : null}
      <label className={styles.field}>
        <span className={styles.fieldLabel}>{t('usersNewPassword')}</span>
        <input
          className={styles.input}
          type="password"
          value={password}
          autoFocus
          onChange={(event) => setPassword(event.target.value)}
        />
        <span className={styles.hint}>{t('usersResetPasswordHint')}</span>
      </label>
    </Modal>
  )
}