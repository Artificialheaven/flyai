import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import { getConfig, putConfig, type AccessSettingsView } from '../../../services/flyai/admin.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { Button } from '../../base/Button.js'
import { showToast } from '../../base/toastState.js'
import { errorText, NumberField, PanelEmpty, PanelError } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

/** 权限门槛（仅管理员）：低于阈值的用户看不到/用不了对应功能；0 = 不限制，admin 恒通过 */
type FormState = {
  browser: string
  pixiv: string
  galgame: string
  agent: string
}

const toForm = (access?: AccessSettingsView): FormState => ({
  browser: String(access?.browser_min_level ?? 0),
  pixiv: String(access?.pixiv_min_level ?? 0),
  galgame: String(access?.galgame_min_level ?? 0),
  agent: String(access?.agent_min_level ?? 0),
})

/** 空串按 0 处理；其余必须是 0–255 的整数（admin=256 恒通过，不落配置） */
const parseLevel = (value: string): number | undefined => {
  const text = value.trim()
  if (!text) {
    return 0
  }
  const parsed = Number(text)
  return Number.isInteger(parsed) && parsed >= 0 && parsed <= 255 ? parsed : undefined
}

export const AccessPanel = () => {
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
      setForm(toForm(config.access))
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
    const access = {
      browser_min_level: parseLevel(form.browser),
      pixiv_min_level: parseLevel(form.pixiv),
      galgame_min_level: parseLevel(form.galgame),
      agent_min_level: parseLevel(form.agent),
    }
    if (Object.values(access).some((value) => value === undefined)) {
      setError(t('accessLevelInvalid'))
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({ access })
      setForm(toForm(saved.access))
      showToast({ key: 'accessSaved' })
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
          <div className={styles.title}>{t('settingDialogAsideAccessOptionLabel')}</div>
          <div className={styles.subtitle}>{t('accessSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button size="s" disabled={!form || saving} onClick={() => void save()}>
            {t(saving ? 'adminSaving' : 'adminSave')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}
      {loading || !form ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : (
        <>
          <div className={styles.sectionTitle}>{t('accessThresholdTitle')}</div>
          <div className={styles.hint}>{t('accessThresholdHint')}</div>
          <div className={styles.rows}>
            <NumberField
              label={t('accessBrowser')}
              hint={t('accessBrowserHint')}
              value={form.browser}
              onChange={(value) => patch({ browser: value })}
            />
            <NumberField
              label={t('accessPixiv')}
              hint={t('accessPixivHint')}
              value={form.pixiv}
              onChange={(value) => patch({ pixiv: value })}
            />
            <NumberField
              label={t('accessGalgame')}
              hint={t('accessGalgameHint')}
              value={form.galgame}
              onChange={(value) => patch({ galgame: value })}
            />
            <NumberField
              label={t('accessAgent')}
              hint={t('accessAgentHint')}
              value={form.agent}
              onChange={(value) => patch({ agent: value })}
            />
          </div>
          <div className={styles.hint}>{t('accessAgentNote')}</div>
        </>
      )}
    </div>
  )
}