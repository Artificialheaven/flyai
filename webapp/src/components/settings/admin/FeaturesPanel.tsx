import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  getConfig,
  getMySettings,
  putMySettings,
  type AdminConfig,
  type MySettingsPayload,
} from '../../../services/flyai/admin.js'
import { Button } from '../../base/Button.js'
import { showToast } from '../../base/toastState.js'
import {
  errorText,
  NumberField,
  PanelEmpty,
  PanelError,
  SwitchRow,
  TriRow,
  type TriValue,
} from './AdminCommon.js'
import styles from './AdminPanels.module.css'

/** 个人「功能」设置：覆盖项可跟随管理员全局默认，记忆/上下文为纯个人字段 */
type FormState = {
  keepTurns: string
  autoRecall: boolean
  autoRecallMax: string
  extractEvery: string
  loreAutoInject: boolean
  maxTokens: string
  toolsEnabled: TriValue
  toolMaxIterations: string
  toolTimeout: string
  rpFormat: TriValue
  rpAttributes: TriValue
  introChars: string
  backgroundChars: string
  worldChars: string
  mesExampleChars: string
  pinnedChars: string
  web: TriValue
  browser: TriValue
  pixiv: TriValue
  image: TriValue
  galgame: TriValue
  galgameLoops: string
}

const tri = (value: boolean | null | undefined): TriValue =>
  value === true ? 'on' : value === false ? 'off' : 'inherit'

const optText = (value: number | null | undefined): string =>
  value === null || value === undefined ? '' : String(value)

/** 空串 → null（跟随默认）；否则必须是整数 */
const parseOptInt = (value: string): number | null | undefined => {
  if (!value.trim()) {
    return null
  }
  const parsed = Number(value)
  return Number.isInteger(parsed) ? parsed : undefined
}

/** 校验通过后把 undefined 收拢为 null（undefined 已在保存前拒绝） */
const optional = (value: number | null | undefined): number | null => value ?? null

const parseIntOr = (value: string, fallback: number): number => {
  const parsed = Number(value)
  return value.trim() && Number.isInteger(parsed) ? parsed : fallback
}

const toForm = (settings: Awaited<ReturnType<typeof getMySettings>>): FormState => ({
  keepTurns: String(settings.keep_turns ?? 0),
  autoRecall: Boolean(settings.auto_recall),
  autoRecallMax: String(settings.auto_recall_max ?? 5),
  extractEvery: String(settings.extract_every_turns ?? 10),
  loreAutoInject: Boolean(settings.lore_auto_inject),
  maxTokens: optText(settings.max_tokens),
  toolsEnabled: tri(settings.tools_enabled),
  toolMaxIterations: optText(settings.tool_max_iterations),
  toolTimeout: optText(settings.tool_timeout_seconds),
  rpFormat: tri(settings.roleplay_format_enabled),
  rpAttributes: tri(settings.roleplay_attributes_enabled),
  introChars: optText(settings.intro_chars),
  backgroundChars: optText(settings.background_chars),
  worldChars: optText(settings.world_chars),
  mesExampleChars: optText(settings.mes_example_chars),
  pinnedChars: optText(settings.pinned_chars),
  web: tri(settings.web_enabled),
  browser: tri(settings.browser_enabled),
  pixiv: tri(settings.pixiv_enabled),
  image: tri(settings.image_enabled),
  galgame: tri(settings.galgame_enabled),
  galgameLoops: optText(settings.galgame_max_loops),
})

export const FeaturesPanel = () => {
  const t = useTranslate()
  const [form, setForm] = useState<FormState | null>(null)
  const [global, setGlobal] = useState<AdminConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [settings, config] = await Promise.all([getMySettings(), getConfig()])
      setForm(toForm(settings))
      setGlobal(config)
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

  const globalInt = (value: number | null | undefined): string | undefined =>
    value === null || value === undefined ? undefined : t('featuresGlobalValue', { value })

  const save = async () => {
    if (!form || saving) {
      return
    }
    const ints = {
      tool_max_iterations: parseOptInt(form.toolMaxIterations),
      tool_timeout_seconds: parseOptInt(form.toolTimeout),
      intro_chars: parseOptInt(form.introChars),
      background_chars: parseOptInt(form.backgroundChars),
      world_chars: parseOptInt(form.worldChars),
      mes_example_chars: parseOptInt(form.mesExampleChars),
      pinned_chars: parseOptInt(form.pinnedChars),
      galgame_max_loops: parseOptInt(form.galgameLoops),
      max_tokens: parseOptInt(form.maxTokens),
    } as const
    if (Object.values(ints).some((value) => value === undefined)) {
      setError(t('featuresNumberInvalid'))
      return
    }
    const triBool = (value: TriValue): boolean | null =>
      value === 'inherit' ? null : value === 'on'
    const payload: MySettingsPayload = {
      keep_turns: parseIntOr(form.keepTurns, 0),
      auto_recall: form.autoRecall,
      auto_recall_max: parseIntOr(form.autoRecallMax, 5),
      extract_every_turns: parseIntOr(form.extractEvery, 10),
      lore_auto_inject: form.loreAutoInject,
      max_tokens: optional(ints.max_tokens),
      tools_enabled: triBool(form.toolsEnabled),
      tool_max_iterations: optional(ints.tool_max_iterations),
      tool_timeout_seconds: optional(ints.tool_timeout_seconds),
      roleplay_format_enabled: triBool(form.rpFormat),
      roleplay_attributes_enabled: triBool(form.rpAttributes),
      intro_chars: optional(ints.intro_chars),
      background_chars: optional(ints.background_chars),
      world_chars: optional(ints.world_chars),
      mes_example_chars: optional(ints.mes_example_chars),
      pinned_chars: optional(ints.pinned_chars),
      web_enabled: triBool(form.web),
      browser_enabled: triBool(form.browser),
      pixiv_enabled: triBool(form.pixiv),
      image_enabled: triBool(form.image),
      galgame_enabled: triBool(form.galgame),
      galgame_max_loops: optional(ints.galgame_max_loops),
    }
    setSaving(true)
    setError('')
    try {
      const saved = await putMySettings(payload)
      setForm(toForm(saved))
      showToast({ key: 'featuresSaved' })
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideFeaturesOptionLabel')}</div>
          <div className={styles.subtitle}>{t('featuresSubtitle')}</div>
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
          <div className={styles.sectionTitle}>{t('featuresMemoryTitle')}</div>
          <div className={styles.rows}>
            <NumberField
              label={t('featuresKeepTurns')}
              hint={t('featuresKeepTurnsHint')}
              value={form.keepTurns}
              onChange={(value) => patch({ keepTurns: value })}
            />
            <SwitchRow
              label={t('featuresAutoRecall')}
              desc={t('featuresAutoRecallDesc')}
              checked={form.autoRecall}
              onChange={(checked) => patch({ autoRecall: checked })}
            />
            <NumberField
              label={t('featuresAutoRecallMax')}
              hint={t('featuresAutoRecallMaxHint')}
              value={form.autoRecallMax}
              onChange={(value) => patch({ autoRecallMax: value })}
            />
            <NumberField
              label={t('featuresExtractEvery')}
              hint={t('featuresExtractEveryHint')}
              value={form.extractEvery}
              onChange={(value) => patch({ extractEvery: value })}
            />
            <SwitchRow
              label={t('featuresLoreAutoInject')}
              desc={t('featuresLoreAutoInjectDesc')}
              checked={form.loreAutoInject}
              onChange={(checked) => patch({ loreAutoInject: checked })}
            />
            <NumberField
              label={t('featuresMaxTokens')}
              hint={t('featuresMaxTokensHint')}
              value={form.maxTokens}
              onChange={(value) => patch({ maxTokens: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('featuresOverrideTitle')}</div>
          <div className={styles.hint}>{t('featuresOverrideHint')}</div>
          <div className={styles.rows}>
            <TriRow
              label={t('featuresTools')}
              desc={t('featuresToolsDesc')}
              value={form.toolsEnabled === 'inherit' ? null : form.toolsEnabled === 'on'}
              globalValue={global?.tools.enabled}
              onChange={(value) => patch({ toolsEnabled: tri(value) })}
            />
            <NumberField
              label={t('featuresMaxIterations')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.tools.max_iterations)}
              value={form.toolMaxIterations}
              onChange={(value) => patch({ toolMaxIterations: value })}
            />
            <NumberField
              label={t('featuresTimeout')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.tools.timeout_seconds)}
              value={form.toolTimeout}
              onChange={(value) => patch({ toolTimeout: value })}
            />
            <TriRow
              label={t('featuresRoleplayFormat')}
              desc={t('featuresRoleplayFormatDesc')}
              value={form.rpFormat === 'inherit' ? null : form.rpFormat === 'on'}
              globalValue={global?.roleplay.format_enabled}
              onChange={(value) => patch({ rpFormat: tri(value) })}
            />
            <TriRow
              label={t('featuresRoleplayAttributes')}
              value={form.rpAttributes === 'inherit' ? null : form.rpAttributes === 'on'}
              globalValue={global?.roleplay.attributes?.enabled}
              onChange={(value) => patch({ rpAttributes: tri(value) })}
            />
            <NumberField
              label={t('featuresIntroChars')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.roleplay.intro_chars)}
              value={form.introChars}
              onChange={(value) => patch({ introChars: value })}
            />
            <NumberField
              label={t('featuresBackgroundChars')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.roleplay.background_chars)}
              value={form.backgroundChars}
              onChange={(value) => patch({ backgroundChars: value })}
            />
            <NumberField
              label={t('featuresWorldChars')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.roleplay.world_chars)}
              value={form.worldChars}
              onChange={(value) => patch({ worldChars: value })}
            />
            <NumberField
              label={t('featuresMesExampleChars')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.roleplay.mes_example_chars)}
              value={form.mesExampleChars}
              onChange={(value) => patch({ mesExampleChars: value })}
            />
            <NumberField
              label={t('featuresPinnedChars')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.roleplay.pinned_chars)}
              value={form.pinnedChars}
              onChange={(value) => patch({ pinnedChars: value })}
            />
            <TriRow
              label={t('featuresWeb')}
              value={form.web === 'inherit' ? null : form.web === 'on'}
              globalValue={global?.web_search.enabled}
              onChange={(value) => patch({ web: tri(value) })}
            />
            <TriRow
              label={t('featuresBrowser')}
              value={form.browser === 'inherit' ? null : form.browser === 'on'}
              globalValue={global?.browser.enabled}
              onChange={(value) => patch({ browser: tri(value) })}
            />
            <TriRow
              label={t('featuresPixiv')}
              value={form.pixiv === 'inherit' ? null : form.pixiv === 'on'}
              globalValue={global?.pixiv.enabled}
              onChange={(value) => patch({ pixiv: tri(value) })}
            />
            <TriRow
              label={t('featuresImage')}
              value={form.image === 'inherit' ? null : form.image === 'on'}
              globalValue={global?.image_generation.enabled}
              onChange={(value) => patch({ image: tri(value) })}
            />
            <TriRow
              label={t('featuresGalgame')}
              desc={t('featuresGalgameDesc')}
              value={form.galgame === 'inherit' ? null : form.galgame === 'on'}
              globalValue={global?.galgame.enabled}
              onChange={(value) => patch({ galgame: tri(value) })}
            />
            <NumberField
              label={t('featuresGalgameLoops')}
              placeholder={t('adminInherit')}
              hint={globalInt(global?.galgame.max_loops)}
              value={form.galgameLoops}
              onChange={(value) => patch({ galgameLoops: value })}
            />
          </div>
        </>
      )}
    </div>
  )
}