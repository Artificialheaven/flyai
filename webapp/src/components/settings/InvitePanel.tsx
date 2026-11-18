import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { getConfig, putConfig } from '../../services/flyai/admin.js'
import { useAuth } from '../../services/flyai/auth.js'
import {
  deleteAdminInvite,
  getMyInvite,
  listAdminInvites,
  regenerateMyInvite,
  setAdminInvite,
  type AdminInviteView,
  type InviteView,
} from '../../services/flyai/invite.js'
import { copyText } from '../../utils/clipboard.js'
import { formatDate } from '../../utils/format.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { showToast } from '../base/toastState.js'
import {
  errorText,
  NumberField,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TextField,
} from './admin/AdminCommon.js'
import styles from './admin/AdminPanels.module.css'

const copy = async (text: string) => {
  const ok = await copyText(text)
  showToast({ key: ok ? 'copied' : 'copyFailed' })
}

const formatTime = (epochSeconds: number): string =>
  formatDate(epochSeconds * 1000) ?? ''

const statusLabel = (
  invite: InviteView
): 'inviteStatusUsable' | 'inviteStatusExhausted' =>
  invite.status === 'usable' ? 'inviteStatusUsable' : 'inviteStatusExhausted'

/** 邀请码：所有人可查看/生成自己的码；管理员额外可设默认额度、自定义码、管理全部码 */
export const InvitePanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [invite, setInvite] = useState<InviteView | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setInvite(await getMyInvite())
    } catch (err) {
      setError(t('inviteLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const regenerate = async () => {
    if (busy) {
      return
    }
    setBusy(true)
    setError('')
    try {
      setInvite(await regenerateMyInvite())
      setConfirming(false)
      showToast({ key: 'inviteGenerated' })
    } catch (err) {
      setError(t('inviteCreateFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideInviteOptionLabel')}</div>
          <div className={styles.subtitle}>{t('inviteSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <div className={styles.sectionTitle}>{t('inviteMyTitle')}</div>
          {invite ? (
            <Button
              variant="outlined"
              size="s"
              disabled={busy}
              onClick={() => setConfirming(true)}
            >
              {t('inviteRegenerate')}
            </Button>
          ) : null}
        </div>
        {loading ? (
          <PanelEmpty text={t('adminLoading')} />
        ) : !invite ? (
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowDesc}>{t('inviteNone')}</div>
            </div>
            <Button size="s" disabled={busy} onClick={() => void regenerate()}>
              {t('inviteGenerate')}
            </Button>
          </div>
        ) : (
          <>
            <div className={styles.item}>
              <div className={styles.itemMain}>
                <div className={styles.itemName}>
                  <span className={styles.code}>{invite.code}</span>
                  <span
                    className={
                      invite.status === 'usable'
                        ? `${styles.badge} ${styles.badgeBrand}`
                        : styles.badge
                    }
                  >
                    {t(statusLabel(invite))}
                  </span>
                </div>
                <div className={styles.itemMeta}>
                  {t('inviteMeta', {
                    used: invite.used,
                    max: invite.max_uses,
                    remaining: invite.remaining,
                  })}
                </div>
              </div>
              <div className={styles.itemActions}>
                <Button size="s" variant="outlined" onClick={() => void copy(invite.code)}>
                  {t('copy')}
                </Button>
              </div>
            </div>
            <div className={styles.sectionTitle}>{t('inviteUsesTitle')}</div>
            {invite.uses.length === 0 ? (
              <div className={styles.muted}>{t('inviteUsesEmpty')}</div>
            ) : (
              <div className={styles.list}>
                {invite.uses.map((use) => (
                  <div key={use.uid} className={styles.quotaUser}>
                    <span>{use.name}</span>
                    <span>{formatTime(use.at)}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {isAdmin ? (
        <AdminInviteSections
          onChanged={() => {
            void load()
          }}
        />
      ) : null}

      {confirming ? (
        <Modal
          title={t('inviteRegenerateTitle')}
          className={styles.dialog}
          onClose={() => (busy ? undefined : setConfirming(false))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                {t('cancel')}
              </Button>
              <Button size="m" disabled={busy} onClick={() => void regenerate()}>
                {t('inviteRegenerate')}
              </Button>
            </div>
          }
        >
          {t('inviteRegenerateDesc')}
        </Modal>
      ) : null}
    </div>
  )
}

/** 管理员专属：注册默认额度、自定义自己的码、全部邀请码 */
const AdminInviteSections = ({ onChanged }: { onChanged: () => void }) => {
  const t = useTranslate()
  const [quota, setQuota] = useState('0')
  // 后端缺省为 true；旧配置没有该字段时保持「需要邀请码」
  const [requireInvite, setRequireInvite] = useState(true)
  const [quotaBusy, setQuotaBusy] = useState(false)
  const [quotaError, setQuotaError] = useState('')
  const [customCode, setCustomCode] = useState('')
  const [maxUses, setMaxUses] = useState('5')
  const [customBusy, setCustomBusy] = useState(false)
  const [customError, setCustomError] = useState('')
  const [all, setAll] = useState<AdminInviteView[]>([])
  const [allLoading, setAllLoading] = useState(true)
  const [allError, setAllError] = useState('')
  const [deleting, setDeleting] = useState<AdminInviteView | null>(null)
  const [busyCode, setBusyCode] = useState('')

  const loadQuota = useCallback(async () => {
    try {
      const config = await getConfig()
      setQuota(String(config.registration?.default_quota ?? 0))
      setRequireInvite(config.registration?.require_invite !== false)
    } catch (err) {
      setQuotaError(t('inviteLoadFailed', { message: errorText(err) }))
    }
  }, [t])

  const loadAll = useCallback(async () => {
    setAllLoading(true)
    setAllError('')
    try {
      setAll(await listAdminInvites())
    } catch (err) {
      setAllError(t('inviteLoadFailed', { message: errorText(err) }))
    } finally {
      setAllLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadQuota()
    void loadAll()
  }, [loadQuota, loadAll])

  const saveQuota = async () => {
    if (quotaBusy) {
      return
    }
    const value = Number(quota)
    if (!Number.isFinite(value) || (value < 0 && value !== -1)) {
      setQuotaError(t('inviteDefaultQuotaInvalid'))
      return
    }
    setQuotaBusy(true)
    setQuotaError('')
    try {
      // 两个字段必须一起提交：后端 parse_obj 会把缺省的 require_invite 重置为 true
      await putConfig({ registration: { default_quota: value, require_invite: requireInvite } })
      showToast({ key: 'inviteDefaultQuotaSaved' })
    } catch (err) {
      setQuotaError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setQuotaBusy(false)
    }
  }

  const saveCustom = async () => {
    if (customBusy) {
      return
    }
    const value = Number(maxUses)
    if (!Number.isInteger(value) || value < 1 || value > 10000) {
      setCustomError(t('inviteMaxUsesInvalid'))
      return
    }
    setCustomBusy(true)
    setCustomError('')
    try {
      await setAdminInvite(customCode.trim(), value)
      setCustomCode('')
      showToast({ key: 'adminSaved' })
      onChanged()
      await loadAll()
    } catch (err) {
      setCustomError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setCustomBusy(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleting || busyCode) {
      return
    }
    setBusyCode(deleting.code)
    setAllError('')
    try {
      await deleteAdminInvite(deleting.code)
      setDeleting(null)
      showToast({ key: 'inviteDeleted' })
      onChanged()
      await loadAll()
    } catch (err) {
      setAllError(t('inviteDeleteFailed', { message: errorText(err) }))
    } finally {
      setBusyCode('')
    }
  }

  return (
    <>
      <div className={styles.card}>
        <div className={styles.sectionTitle}>{t('inviteDefaultQuota')}</div>
        {quotaError ? <PanelError message={quotaError} /> : null}
        <SwitchRow
          label={t('inviteRequireSwitch')}
          desc={t('inviteRequireSwitchDesc')}
          checked={requireInvite}
          onChange={setRequireInvite}
        />
        <NumberField
          label={t('inviteDefaultQuota')}
          hint={t('inviteDefaultQuotaHint')}
          value={quota}
          onChange={setQuota}
        />
        <div className={styles.actions}>
          <Button size="s" disabled={quotaBusy} onClick={() => void saveQuota()}>
            {t(quotaBusy ? 'adminSaving' : 'inviteSave')}
          </Button>
        </div>
      </div>

      <div className={styles.card}>
        <div className={styles.sectionTitle}>{t('inviteCustomTitle')}</div>
        {customError ? <PanelError message={customError} /> : null}
        <TextField
          label={t('inviteCustomCode')}
          hint={t('inviteCustomCodeHint')}
          value={customCode}
          onChange={setCustomCode}
        />
        <NumberField
          label={t('inviteMaxUses')}
          hint={t('inviteMaxUsesHint')}
          value={maxUses}
          onChange={setMaxUses}
        />
        <div className={styles.actions}>
          <Button size="s" disabled={customBusy} onClick={() => void saveCustom()}>
            {t(customBusy ? 'adminSaving' : 'inviteSave')}
          </Button>
        </div>
      </div>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <div className={styles.sectionTitle}>{t('inviteAllTitle')}</div>
          <Button variant="outlined" size="s" disabled={allLoading} onClick={() => void loadAll()}>
            {t('adminRefresh')}
          </Button>
        </div>
        {allError ? <PanelError message={allError} /> : null}
        {allLoading ? (
          <PanelEmpty text={t('adminLoading')} />
        ) : all.length === 0 ? (
          <PanelEmpty text={t('inviteAllEmpty')} />
        ) : (
          <div className={styles.list}>
            {all.map((item) => (
              <div key={item.code} className={styles.item}>
                <div className={styles.itemMain}>
                  <div className={styles.itemName}>
                    <span className={styles.code}>{item.code}</span>
                    <span
                      className={
                        item.status === 'usable'
                          ? `${styles.badge} ${styles.badgeBrand}`
                          : styles.badge
                      }
                    >
                      {t(statusLabel(item))}
                    </span>
                  </div>
                  <div className={styles.itemMeta}>
                    {t('inviteOwnerLine', {
                      owner: item.owner_name || t('inviteUnknownOwner'),
                      used: item.used,
                      max: item.max_uses,
                    })}
                  </div>
                </div>
                <div className={styles.itemActions}>
                  <Button size="s" variant="outlined" onClick={() => void copy(item.code)}>
                    {t('copy')}
                  </Button>
                  <Button
                    size="s"
                    variant="dangerOutlined"
                    disabled={busyCode === item.code}
                    onClick={() => setDeleting(item)}
                  >
                    {t('adminDelete')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => (busyCode ? undefined : setDeleting(null))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busyCode)}
                onClick={() => setDeleting(null)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                size="m"
                disabled={Boolean(busyCode)}
                onClick={() => void confirmDelete()}
              >
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('inviteDeleteDesc', { code: deleting.code })}
        </Modal>
      ) : null}
    </>
  )
}
