import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  getConfig,
  putConfig,
  testDeepSeek,
  type DeepSeekSettingsView,
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

/** DeepSeek 官方服务（仅管理员）：搜索/抓取/TTS/语音识别替代实现，按量扣用户额度 */
type FormState = {
  enabled: boolean
  apiKey: string
  baseUrl: string
  proxy: string
  timeout: string
  searchEnabled: boolean
  fetchEnabled: boolean
  ttsEnabled: boolean
  asrEnabled: boolean
  searchPrice: string
  fetchPrice: string
  ttsPrice: string
  asrPrice: string
  ttsVoice: 'stella' | 'tide'
}

const toForm = (ds?: DeepSeekSettingsView): FormState => ({
  enabled: ds?.enabled ?? true,
  apiKey: ds?.api_key ?? '',
  baseUrl: ds?.base_url ?? 'https://api.deepseek.com',
  proxy: ds?.proxy ?? '',
  timeout: String(ds?.timeout_seconds ?? 60),
  searchEnabled: ds?.search_enabled ?? true,
  fetchEnabled: ds?.fetch_enabled ?? true,
  ttsEnabled: ds?.tts_enabled ?? true,
  asrEnabled: ds?.asr_enabled ?? true,
  searchPrice: String(ds?.search_price ?? 5),
  fetchPrice: String(ds?.fetch_price ?? 5),
  ttsPrice: String(ds?.tts_price ?? 3),
  asrPrice: String(ds?.asr_price ?? 1),
  ttsVoice: ds?.tts_voice === 'tide' ? 'tide' : 'stella',
})

/** 价格允许空串（按 0 处理）与非负小数；非法返回 undefined */
const parsePrice = (value: string): number | undefined => {
  const text = value.trim()
  if (!text) {
    return 0
  }
  const parsed = Number(text)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined
}

/** 超时必须是正整数秒 */
const parseTimeout = (value: string): number | undefined => {
  const parsed = Number(value.trim())
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
}

export const DeepSeekPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [keySet, setKeySet] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setForm(toForm(config.deepseek))
      setKeySet(Boolean(config.deepseek?.key_set))
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
    const prices = {
      search_price: parsePrice(form.searchPrice),
      fetch_price: parsePrice(form.fetchPrice),
      tts_price: parsePrice(form.ttsPrice),
      asr_price: parsePrice(form.asrPrice),
    }
    const timeout = parseTimeout(form.timeout)
    if (Object.values(prices).some((value) => value === undefined) || timeout === undefined) {
      setError(t('deepseekNumberInvalid'))
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putConfig({
        deepseek: {
          enabled: form.enabled,
          api_key: form.apiKey,
          base_url: form.baseUrl.trim(),
          proxy: form.proxy.trim(),
          timeout_seconds: timeout,
          search_enabled: form.searchEnabled,
          fetch_enabled: form.fetchEnabled,
          tts_enabled: form.ttsEnabled,
          asr_enabled: form.asrEnabled,
          search_price: prices.search_price,
          fetch_price: prices.fetch_price,
          tts_price: prices.tts_price,
          asr_price: prices.asr_price,
          tts_voice: form.ttsVoice,
        },
      })
      setForm(toForm(saved.deepseek))
      setKeySet(Boolean(saved.deepseek?.key_set))
      showToast({ key: 'deepseekSaved' })
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setSaving(false)
    }
  }

  const testKey = async () => {
    if (!form || testing) {
      return
    }
    // 前端的 Key 框显示掩码；出现非掩码内容＝填了新 Key 还没保存，测的会是旧 Key
    const typed = form.apiKey.trim()
    if (typed && !typed.includes('****')) {
      showToast(t('deepseekTestNoKey'))
      return
    }
    if (!keySet) {
      showToast(t('deepseekTestNoKey'))
      return
    }
    setTesting(true)
    try {
      const result = await testDeepSeek()
      showToast(result.message)
    } catch (err) {
      showToast(t('deepseekTestFailed', { message: errorText(err) }))
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
          <div className={styles.title}>{t('settingDialogAsideDeepseekOptionLabel')}</div>
          <div className={styles.subtitle}>{t('deepseekSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button
            variant="outlined"
            size="s"
            disabled={!form || loading || testing}
            onClick={() => void testKey()}
          >
            {t(testing ? 'deepseekTesting' : 'deepseekTest')}
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
          <div className={styles.sectionTitle}>{t('deepseekAccountTitle')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('deepseekEnabled')}
              desc={t('deepseekEnabledDesc')}
              checked={form.enabled}
              onChange={(checked) => patch({ enabled: checked })}
            />
            <TextField
              label={t('deepseekApiKey')}
              hint={t(keySet ? 'deepseekKeySetHint' : 'deepseekKeyUnsetHint')}
              value={form.apiKey}
              placeholder={t('deepseekApiKeyPlaceholder')}
              onChange={(value) => patch({ apiKey: value })}
            />
            <TextField
              label={t('deepseekBaseUrl')}
              hint={t('deepseekBaseUrlHint')}
              value={form.baseUrl}
              onChange={(value) => patch({ baseUrl: value })}
            />
            <TextField
              label={t('deepseekProxy')}
              hint={t('deepseekProxyHint')}
              value={form.proxy}
              onChange={(value) => patch({ proxy: value })}
            />
            <NumberField
              label={t('deepseekTimeout')}
              hint={t('deepseekTimeoutHint')}
              value={form.timeout}
              onChange={(value) => patch({ timeout: value })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('deepseekTtsVoice')}</div>
                <div className={styles.rowDesc}>{t('deepseekTtsVoiceDesc')}</div>
              </div>
              <Select<'stella' | 'tide'>
                value={form.ttsVoice}
                ariaLabel={t('deepseekTtsVoice')}
                options={[
                  { value: 'stella', label: t('deepseekTtsVoiceStella') },
                  { value: 'tide', label: t('deepseekTtsVoiceTide') },
                ]}
                onChange={(value) => patch({ ttsVoice: value })}
              />
            </div>
          </div>
          <div className={styles.hint}>{t('deepseekKeyNote')}</div>

          <div className={styles.sectionTitle}>{t('deepseekServiceTitle')}</div>
          <div className={styles.hint}>{t('deepseekServiceHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('deepseekSearch')}
              desc={t('deepseekSearchDesc')}
              checked={form.searchEnabled}
              onChange={(checked) => patch({ searchEnabled: checked })}
            />
            <NumberField
              label={t('deepseekSearchPrice')}
              hint={t('deepseekPerThousand')}
              value={form.searchPrice}
              onChange={(value) => patch({ searchPrice: value })}
            />
            <SwitchRow
              label={t('deepseekFetch')}
              desc={t('deepseekFetchDesc')}
              checked={form.fetchEnabled}
              onChange={(checked) => patch({ fetchEnabled: checked })}
            />
            <NumberField
              label={t('deepseekFetchPrice')}
              hint={t('deepseekPerThousand')}
              value={form.fetchPrice}
              onChange={(value) => patch({ fetchPrice: value })}
            />
            <SwitchRow
              label={t('deepseekTts')}
              desc={t('deepseekTtsDesc')}
              checked={form.ttsEnabled}
              onChange={(checked) => patch({ ttsEnabled: checked })}
            />
            <NumberField
              label={t('deepseekTtsPrice')}
              hint={t('deepseekPerTenThousandChars')}
              value={form.ttsPrice}
              onChange={(value) => patch({ ttsPrice: value })}
            />
            <SwitchRow
              label={t('deepseekAsr')}
              desc={t('deepseekAsrDesc')}
              checked={form.asrEnabled}
              onChange={(checked) => patch({ asrEnabled: checked })}
            />
            <NumberField
              label={t('deepseekAsrPrice')}
              hint={t('deepseekPerHour')}
              value={form.asrPrice}
              onChange={(value) => patch({ asrPrice: value })}
            />
          </div>
          <div className={styles.hint}>{t('deepseekFallbackNote')}</div>
        </>
      )}
    </div>
  )
}