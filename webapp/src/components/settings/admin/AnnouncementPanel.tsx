import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import { getConfig, putConfig, type AnnouncementView } from '../../../services/flyai/admin.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { Button } from '../../base/Button.js'
import { showToast } from '../../base/toastState.js'
import {
  errorText,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TextAreaField,
  TextField,
} from './AdminCommon.js'
import styles from './AdminPanels.module.css'

/** 启动公告（仅管理员）：每次打开应用（含登录页）都会弹出，不做已读持久化 */

type FormState = {
  enabled: boolean
  title: string
  content: string
}

const toForm = (view?: AnnouncementView): FormState => ({
  enabled: view?.enabled ?? false,
  title: view?.title ?? '',
  content: view?.content ?? '',
})

export const AnnouncementPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setForm(toForm(config.announcement))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const patch = (next: Partial<FormState>) =>
    setForm((current) => (current ? { ...current, ...next } : current))

  const save = async () => {
    if (!form || saving) {
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({
        announcement: {
          enabled: form.enabled,
          title: form.title.trim(),
          content: form.content,
        },
      })
      setForm(toForm(saved.announcement))
      showToast({ key: 'adminSaved' })
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setSaving(false)
    }
  }

  if (!isAdmin) {
    return (
      <div className={styles.page}>
        <PanelEmpty text={t('systemNotAdmin')} />
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideAnnouncementOptionLabel')}</div>
          <div className={styles.subtitle}>{t('announcementSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button size="s" disabled={!form || loading || saving} onClick={() => void save()}>
            {t(saving ? 'adminSaving' : 'inviteSave')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}
      {loading || !form ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : (
        <div className={styles.rows}>
          <SwitchRow
            label={t('announcementEnabled')}
            desc={t('announcementEnabledDesc')}
            checked={form.enabled}
            onChange={(checked) => patch({ enabled: checked })}
          />
          <TextField
            label={t('announcementTitleLabel')}
            value={form.title}
            placeholder={t('announcementTitlePlaceholder')}
            onChange={(value) => patch({ title: value })}
          />
          <TextAreaField
            label={t('announcementContent')}
            hint={t('announcementContentHint')}
            value={form.content}
            placeholder={t('announcementContentPlaceholder')}
            onChange={(value) => patch({ content: value })}
          />
        </div>
      )}
    </div>
  )
}
