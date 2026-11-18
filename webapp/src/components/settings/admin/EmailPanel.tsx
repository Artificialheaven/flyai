import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  getConfig,
  putConfig,
  testEmail,
  type EmailSettingsView,
} from '../../../services/flyai/admin.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { Button } from '../../base/Button.js'
import { Select } from '../../base/Select.js'
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

/** 邮件服务（仅管理员）：注册邮箱验证码与找回密码所用的 SMTP 配置 */

type Encryption = 'ssl' | 'starttls' | 'none'

type FormState = {
  enabled: boolean
  host: string
  port: string
  encryption: Encryption
  username: string
  password: string
  fromName: string
  fromAddress: string
  ttl: string
  resend: string
  hourly: string
}

const toForm = (em?: EmailSettingsView): FormState => ({
  enabled: em?.enabled ?? false,
  host: em?.host || 'smtp.qq.com',
  port: String(em?.port ?? 465),
  encryption:
    em?.encryption === 'starttls' || em?.encryption === 'none' ? em.encryption : 'ssl',
  username: em?.username ?? '',
  // 后端只回传掩码；保留原样回存时由后端还原旧授权码
  password: em?.password ?? '',
  fromName: em?.from_name || 'FlyAI',
  fromAddress: em?.from_address ?? '',
  ttl: String(em?.code_ttl_seconds ?? 600),
  resend: String(em?.resend_interval_seconds ?? 60),
  hourly: String(em?.hourly_limit ?? 10),
})

/** 整数且在范围内；非法返回 undefined */
const parseIntIn = (value: string, min: number, max: number): number | undefined => {
  const parsed = Number(value.trim())
  return Number.isInteger(parsed) && parsed >= min && parsed <= max ? parsed : undefined
}

export const EmailPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [passwordSet, setPasswordSet] = useState(false)
  const [testTo, setTestTo] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setForm(toForm(config.email))
      setPasswordSet(Boolean(config.email?.password_set))
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

  /** 保存成功返回 true；保存并测试据此决定是否继续发信 */
  const save = async (): Promise<boolean> => {
    if (!form || saving) {
      return false
    }
    const port = parseIntIn(form.port, 1, 65535)
    const ttl = parseIntIn(form.ttl, 60, 3600)
    const resend = parseIntIn(form.resend, 10, 600)
    const hourly = parseIntIn(form.hourly, 1, 100)
    if (
      port === undefined ||
      ttl === undefined ||
      resend === undefined ||
      hourly === undefined
    ) {
      setError(t('emailNumbersInvalid'))
      return false
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({
        email: {
          enabled: form.enabled,
          host: form.host.trim(),
          port,
          encryption: form.encryption,
          username: form.username.trim(),
          password: form.password,
          from_name: form.fromName.trim(),
          from_address: form.fromAddress.trim(),
          code_ttl_seconds: ttl,
          resend_interval_seconds: resend,
          hourly_limit: hourly,
        },
      })
      setForm(toForm(saved.email))
      setPasswordSet(Boolean(saved.email?.password_set))
      showToast({ key: 'emailSaved' })
      return true
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
      return false
    } finally {
      setSaving(false)
    }
  }

  const saveAndTest = async () => {
    if (!form || saving || testing) {
      return
    }
    // 授权码为掩码或已保存都算已配置；两者皆空说明还没填
    if (!form.username.trim() && !passwordSet && !form.password.trim()) {
      showToast(t('emailNotConfigured'))
      return
    }
    const saved = await save()
    if (!saved) {
      return
    }
    setTesting(true)
    try {
      const result = await testEmail(testTo.trim())
      showToast(result.ok ? result.message : t('emailTestFail', { message: result.message }))
    } catch (err) {
      showToast(t('emailTestFail', { message: errorText(err) }))
    } finally {
      setTesting(false)
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
          <div className={styles.title}>{t('settingDialogAsideEmailOptionLabel')}</div>
          <div className={styles.subtitle}>{t('emailSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button size="s" disabled={!form || loading || saving} onClick={() => void save()}>
            {t(saving ? 'emailSaving' : 'emailSave')}
          </Button>
          <Button
            size="s"
            disabled={!form || loading || saving || testing}
            onClick={() => void saveAndTest()}
          >
            {t(testing ? 'emailTesting' : 'emailSaveAndTest')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}
      {loading || !form ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : (
        <>
          <div className={styles.rows}>
            <SwitchRow
              label={t('emailEnabled')}
              desc={t('emailEnabledDesc')}
              checked={form.enabled}
              onChange={(checked) => patch({ enabled: checked })}
            />
            <div className={styles.inlineFields}>
              <TextField
                label={t('emailHost')}
                value={form.host}
                placeholder="smtp.qq.com"
                onChange={(value) => patch({ host: value })}
              />
              <NumberField
                label={t('emailPort')}
                value={form.port}
                placeholder="465"
                onChange={(value) => patch({ port: value })}
              />
            </div>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('emailEncryption')}</div>
              </div>
              <Select<Encryption>
                value={form.encryption}
                ariaLabel={t('emailEncryption')}
                options={[
                  { value: 'ssl', label: t('emailEncryptionSsl') },
                  { value: 'starttls', label: t('emailEncryptionStarttls') },
                  { value: 'none', label: t('emailEncryptionNone') },
                ]}
                onChange={(value) => patch({ encryption: value })}
              />
            </div>
            <TextField
              label={t('emailUsername')}
              hint={t('emailUsernameHint')}
              value={form.username}
              placeholder="name@qq.com"
              type="email"
              onChange={(value) => patch({ username: value })}
            />
            <TextField
              label={t('emailPassword')}
              hint={t(
                passwordSet ? 'emailPasswordSetHint' : 'emailPasswordUnsetHint'
              )}
              value={form.password}
              placeholder={t('emailPasswordPlaceholder')}
              type="password"
              onChange={(value) => patch({ password: value })}
            />
            <TextField
              label={t('emailFromName')}
              value={form.fromName}
              placeholder="FlyAI"
              onChange={(value) => patch({ fromName: value })}
            />
            <TextField
              label={t('emailFromAddress')}
              hint={t('emailFromAddressHint')}
              value={form.fromAddress}
              placeholder="name@qq.com"
              type="email"
              onChange={(value) => patch({ fromAddress: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('emailLimitTitle')}</div>
          <div className={styles.hint}>{t('emailNumbersHint')}</div>
          <div className={styles.rows}>
            <NumberField
              label={t('emailCodeTtl')}
              value={form.ttl}
              placeholder="600"
              onChange={(value) => patch({ ttl: value })}
            />
            <NumberField
              label={t('emailResendInterval')}
              value={form.resend}
              placeholder="60"
              onChange={(value) => patch({ resend: value })}
            />
            <NumberField
              label={t('emailHourlyLimit')}
              value={form.hourly}
              placeholder="10"
              onChange={(value) => patch({ hourly: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('emailTestTitle')}</div>
          <div className={styles.rows}>
            <TextField
              label={t('emailTestTo')}
              hint={t('emailTestToHint')}
              value={testTo}
              placeholder="name@qq.com"
              type="email"
              onChange={setTestTo}
            />
          </div>
        </>
      )}
    </div>
  )
}
