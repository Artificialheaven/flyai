import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import { getConfig, putConfig, type AdminConfig } from '../../../services/flyai/admin.js'
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

type Raw = Record<string, unknown>
type RawConfig = Record<string, Raw>

const asRecord = (value: unknown): Raw =>
  value && typeof value === 'object' ? (value as Raw) : {}

const num = (raw: Raw, key: string, fallback: number): string => {
  const value = raw[key]
  return typeof value === 'number' ? String(value) : String(fallback)
}

const bool = (raw: Raw, key: string, fallback: boolean): boolean =>
  typeof raw[key] === 'boolean' ? (raw[key] as boolean) : fallback

const str = (raw: Raw, key: string, fallback: string): string =>
  typeof raw[key] === 'string' ? (raw[key] as string) : fallback

type FormState = {
  toolsEnabled: boolean
  toolsMax: string
  toolsTimeout: string
  rpFormat: boolean
  rpAttributes: boolean
  introChars: string
  backgroundChars: string
  worldChars: string
  mesExampleChars: string
  pinnedChars: string
  webEnabled: boolean
  webResults: string
  webMaxChars: string
  webEngine: 'chromium' | 'http'
  webSearchEngine: 'bing' | 'baidu'
  webIterations: string
  browserEnabled: boolean
  browserHeadless: boolean
  pixivEnabled: boolean
  pixivR18: boolean
  pixivCandidates: string
  imageEnabled: boolean
  imageSize: string
  galgameEnabled: boolean
  galgameLoops: string
  ttsEnabled: boolean
  ttsMinLevel: string
  ttsEngine: 'deepseek' | 'doubao'
  ttsApiKey: string
  ttsResourceId: string
  ttsDefaultSpeaker: string
  ttsFormat: 'mp3' | 'ogg_opus' | 'pcm'
  ttsSampleRate: string
  ttsSpeechRate: string
  ttsLoudnessRate: string
  ttsProxy: string
  ttsTimeout: string
  ttsVoices: { name: string; id: string }[]
  loopEnabled: boolean
  sdEnabled: boolean
  sdBaseUrl: string
  sdFnIndex: string
  sdWidth: string
  sdHeight: string
  sdSteps: string
  sdCfg: string
  sdSampler: string
  sdSchedule: string
  sdNegative: string
  moodEnabled: boolean
  moodBaseline: string
  moodDelta: string
  moodConfidence: string
  moodDailyCap: string
  moodTimeline: string
  moodJudgeProvider: string
  moodJudgeModel: string
  selfMemEnabled: boolean
  selfMemQuota: string
  selfMemTopk: string
  selfMemBudget: string
  proactorEnabled: boolean
  proactorCheckMinutes: string
  proactorWakeProb: string
  proactorWakeEveryN: string
  proactorActiveHours: string
  proactorPeak: string
  proactorValley: string
  proactorCooldown: string
  proactorQuiet: string
  proactorPollsMax: string
  mcpEnabled: boolean
  mcpTimeout: string
  mcpMinLevel: string
  extEnabled: boolean
  extToken: string
  extTokenSet: boolean
  extCooldown: string
  extBaseUrl: string
  watchersMax: string
}

const toForm = (config: AdminConfig): FormState => {
  const raw = config as unknown as RawConfig
  const tools = asRecord(raw.tools)
  const roleplay = asRecord(raw.roleplay)
  const attributes = asRecord(roleplay.attributes)
  const web = asRecord(raw.web_search)
  const browser = asRecord(raw.browser)
  const pixiv = asRecord(raw.pixiv)
  const image = asRecord(raw.image_generation)
  const galgame = asRecord(raw.galgame)
  const tts = asRecord(raw.tts)
  const loop = asRecord(raw.loop)
  const sd = asRecord(raw.stable_diffusion)
  const mood = asRecord(raw.mood)
  const selfMemory = asRecord(raw.self_memory)
  const proactor = asRecord(raw.proactor)
  const mcp = asRecord(raw.mcp)
  const extTrigger = asRecord(raw.ext_trigger)
  const watchers = asRecord(raw.watchers)
  const ttsFormatRaw = str(tts, 'format', 'mp3')
  const ttsFormat: 'mp3' | 'ogg_opus' | 'pcm' =
    ttsFormatRaw === 'ogg_opus' || ttsFormatRaw === 'pcm' ? ttsFormatRaw : 'mp3'
  const ttsResourceRaw = str(tts, 'resource_id', 'seed-tts-2.0')
  const ttsResourceId = [
    'seed-tts-2.0',
    'seed-tts-1.0',
    'seed-tts-1.0-concurr',
    'seed-icl-2.0',
  ].includes(ttsResourceRaw)
    ? ttsResourceRaw
    : 'seed-tts-2.0'
  const ttsVoices = Array.isArray(tts.voices)
    ? tts.voices.flatMap((item) => {
        const voice = asRecord(item)
        return typeof voice.id === 'string' ? [{ name: str(voice, 'name', ''), id: voice.id }] : []
      })
    : []
  return {
    toolsEnabled: bool(tools, 'enabled', true),
    toolsMax: num(tools, 'max_iterations', 8),
    toolsTimeout: num(tools, 'timeout_seconds', 120),
    rpFormat: bool(roleplay, 'format_enabled', true),
    rpAttributes: bool(attributes, 'enabled', true),
    introChars: num(roleplay, 'intro_chars', 0),
    backgroundChars: num(roleplay, 'background_chars', 0),
    worldChars: num(roleplay, 'world_chars', 0),
    mesExampleChars: num(roleplay, 'mes_example_chars', 0),
    pinnedChars: num(roleplay, 'pinned_chars', 0),
    webEnabled: bool(web, 'enabled', true),
    webResults: num(web, 'results_num', 8),
    webMaxChars: num(web, 'max_page_chars', 6000),
    webEngine: str(web, 'engine', 'chromium') === 'http' ? 'http' : 'chromium',
    webSearchEngine: str(web, 'search_engine', 'bing') === 'baidu' ? 'baidu' : 'bing',
    webIterations: num(web, 'max_iterations', 15),
    browserEnabled: bool(browser, 'enabled', false),
    browserHeadless: bool(browser, 'headless', true),
    pixivEnabled: bool(pixiv, 'enabled', true),
    pixivR18: bool(pixiv, 'r18_allowed', false),
    pixivCandidates: num(pixiv, 'candidate_num', 15),
    imageEnabled: bool(image, 'enabled', true),
    imageSize: str(image, 'default_size', '2K'),
    galgameEnabled: bool(galgame, 'enabled', true),
    galgameLoops: num(galgame, 'max_loops', 40),
    ttsEnabled: bool(tts, 'enabled', true),
    ttsMinLevel: num(tts, 'min_level', 0),
    ttsEngine: str(tts, 'engine', 'deepseek') === 'doubao' ? 'doubao' : 'deepseek',
    ttsApiKey: str(tts, 'api_key', ''),
    ttsResourceId,
    ttsDefaultSpeaker: str(tts, 'default_speaker', ''),
    ttsFormat,
    ttsSampleRate: num(tts, 'sample_rate', 24000),
    ttsSpeechRate: num(tts, 'speech_rate', 0),
    ttsLoudnessRate: num(tts, 'loudness_rate', 0),
    ttsProxy: str(tts, 'proxy', ''),
    ttsTimeout: num(tts, 'timeout_seconds', 30),
    ttsVoices,
    loopEnabled: bool(loop, 'enabled', false),
    sdEnabled: bool(sd, 'enabled', false),
    sdBaseUrl: str(sd, 'base_url', 'http://127.0.0.1:7860'),
    sdFnIndex: num(sd, 'fn_index', 99),
    sdWidth: num(sd, 'width', 512),
    sdHeight: num(sd, 'height', 768),
    sdSteps: num(sd, 'steps', 28),
    sdCfg: num(sd, 'cfg_scale', 7),
    sdSampler: str(sd, 'sampler', 'DPM++ 2M'),
    sdSchedule: str(sd, 'schedule', 'Karras'),
    sdNegative: str(sd, 'negative_prompt', ''),
    moodEnabled: bool(mood, 'enabled', false),
    moodBaseline: num(mood, 'baseline', 70),
    moodDelta: num(mood, 'delta_threshold', 5),
    moodConfidence: num(mood, 'min_confidence', 60),
    moodDailyCap: num(mood, 'daily_cap', 6),
    moodTimeline: num(mood, 'timeline_keep', 200),
    moodJudgeProvider: str(mood, 'judge_provider_id', ''),
    moodJudgeModel: str(mood, 'judge_model', ''),
    selfMemEnabled: bool(selfMemory, 'enabled', true),
    selfMemQuota: num(selfMemory, 'daily_quota', 8),
    selfMemTopk: num(selfMemory, 'recall_topk', 5),
    selfMemBudget: num(selfMemory, 'recall_budget', 300),
    proactorEnabled: bool(proactor, 'enabled', false),
    proactorCheckMinutes: num(proactor, 'wake_check_minutes', 30),
    proactorWakeProb: num(proactor, 'wake_prob_percent', 10),
    proactorWakeEveryN: num(proactor, 'wake_every_n', 20),
    proactorActiveHours: num(proactor, 'active_hours', 12),
    proactorPeak: num(proactor, 'speakup_peak_percent', 20),
    proactorValley: num(proactor, 'speakup_valley_percent', 60),
    proactorCooldown: num(proactor, 'self_cooldown_minutes', 20),
    proactorQuiet: str(proactor, 'quiet_hours', '01:00-08:00'),
    proactorPollsMax: num(proactor, 'polls_max_per_session', 10),
    mcpEnabled: bool(mcp, 'enabled', false),
    mcpTimeout: num(mcp, 'timeout', 30),
    mcpMinLevel: num(mcp, 'call_min_level', 0),
    extEnabled: bool(extTrigger, 'enabled', false),
    extToken: '',
    extTokenSet: Boolean(extTrigger.token_set),
    extCooldown: num(extTrigger, 'cooldown_seconds', 30),
    extBaseUrl: str(extTrigger, 'base_url', ''),
    watchersMax: num(watchers, 'max_per_session', 5),
  }
}

/** 系统默认值（仅管理员）：工具、角色扮演、联网、浏览器、Pixiv、生图、Galgame、语音、长跑 */
export const SystemPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [form, setForm] = useState<FormState | null>(null)
  const [raw, setRaw] = useState<RawConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const config = await getConfig()
      setRaw(config as unknown as RawConfig)
      setForm(toForm(config))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    if (isAdmin) {
      void load()
    }
  }, [isAdmin, load])

  const patch = (next: Partial<FormState>) =>
    setForm((current) => (current ? { ...current, ...next } : current))

  const save = async () => {
    if (!form || !raw || saving) {
      return
    }
    const ints: Record<string, number | undefined> = {
      toolsMax: form.toolsMax.trim() ? Number(form.toolsMax) : NaN,
      toolsTimeout: form.toolsTimeout.trim() ? Number(form.toolsTimeout) : NaN,
      introChars: Number(form.introChars),
      backgroundChars: Number(form.backgroundChars),
      worldChars: Number(form.worldChars),
      mesExampleChars: Number(form.mesExampleChars),
      pinnedChars: Number(form.pinnedChars),
      webResults: Number(form.webResults),
      webMaxChars: Number(form.webMaxChars),
      webIterations: Number(form.webIterations),
      pixivCandidates: Number(form.pixivCandidates),
      galgameLoops: Number(form.galgameLoops),
      ttsMinLevel: Number(form.ttsMinLevel),
      ttsSampleRate: Number(form.ttsSampleRate),
      ttsSpeechRate: Number(form.ttsSpeechRate),
      ttsLoudnessRate: Number(form.ttsLoudnessRate),
      ttsTimeout: Number(form.ttsTimeout),
      sdFnIndex: Number(form.sdFnIndex),
      sdWidth: Number(form.sdWidth),
      sdHeight: Number(form.sdHeight),
      sdSteps: Number(form.sdSteps),
      sdCfg: Number(form.sdCfg),
      moodBaseline: Number(form.moodBaseline),
      moodDelta: Number(form.moodDelta),
      moodConfidence: Number(form.moodConfidence),
      moodDailyCap: Number(form.moodDailyCap),
      moodTimeline: Number(form.moodTimeline),
      selfMemQuota: Number(form.selfMemQuota),
      selfMemTopk: Number(form.selfMemTopk),
      selfMemBudget: Number(form.selfMemBudget),
      proactorCheckMinutes: Number(form.proactorCheckMinutes),
      proactorWakeProb: Number(form.proactorWakeProb),
      proactorWakeEveryN: Number(form.proactorWakeEveryN),
      proactorActiveHours: Number(form.proactorActiveHours),
      proactorPeak: Number(form.proactorPeak),
      proactorValley: Number(form.proactorValley),
      proactorCooldown: Number(form.proactorCooldown),
      proactorPollsMax: Number(form.proactorPollsMax),
      mcpTimeout: Number(form.mcpTimeout),
      mcpMinLevel: Number(form.mcpMinLevel),
      extCooldown: Number(form.extCooldown),
      watchersMax: Number(form.watchersMax),
    }
    if (Object.values(ints).some((value) => value === undefined || !Number.isFinite(value))) {
      setError(t('featuresNumberInvalid'))
      return
    }
    const roleplay = asRecord(raw.roleplay)
    const attributes = asRecord(roleplay.attributes)
    // 各段整体回传：未编辑字段（代理、密钥掩码等）原样保留
    const payload: Record<string, unknown> = {
      tools: {
        ...asRecord(raw.tools),
        enabled: form.toolsEnabled,
        max_iterations: ints.toolsMax,
        timeout_seconds: ints.toolsTimeout,
      },
      roleplay: {
        ...roleplay,
        format_enabled: form.rpFormat,
        attributes: { ...attributes, enabled: form.rpAttributes },
        intro_chars: ints.introChars,
        background_chars: ints.backgroundChars,
        world_chars: ints.worldChars,
        mes_example_chars: ints.mesExampleChars,
        pinned_chars: ints.pinnedChars,
      },
      web_search: {
        ...asRecord(raw.web_search),
        enabled: form.webEnabled,
        results_num: ints.webResults,
        max_page_chars: ints.webMaxChars,
        engine: form.webEngine,
        search_engine: form.webSearchEngine,
        max_iterations: ints.webIterations,
      },
      browser: {
        ...asRecord(raw.browser),
        enabled: form.browserEnabled,
        headless: form.browserHeadless,
      },
      pixiv: {
        ...asRecord(raw.pixiv),
        enabled: form.pixivEnabled,
        r18_allowed: form.pixivR18,
        candidate_num: ints.pixivCandidates,
      },
      image_generation: {
        ...asRecord(raw.image_generation),
        enabled: form.imageEnabled,
        default_size: form.imageSize,
      },
      galgame: {
        ...asRecord(raw.galgame),
        enabled: form.galgameEnabled,
        max_loops: ints.galgameLoops,
      },
      tts: {
        ...asRecord(raw.tts),
        enabled: form.ttsEnabled,
        engine: form.ttsEngine,
        api_key: form.ttsApiKey,
        resource_id: form.ttsResourceId,
        voices: form.ttsVoices
          .map((voice) => ({ name: voice.name.trim(), id: voice.id.trim() }))
          .filter((voice) => voice.id),
        default_speaker: form.ttsDefaultSpeaker.trim(),
        min_level: ints.ttsMinLevel,
        format: form.ttsFormat,
        sample_rate: ints.ttsSampleRate,
        speech_rate: ints.ttsSpeechRate,
        loudness_rate: ints.ttsLoudnessRate,
        proxy: form.ttsProxy.trim(),
        timeout_seconds: ints.ttsTimeout,
      },
      loop: {
        ...asRecord(raw.loop),
        enabled: form.loopEnabled,
      },
      stable_diffusion: {
        ...asRecord(raw.stable_diffusion),
        enabled: form.sdEnabled,
        base_url: form.sdBaseUrl.trim(),
        fn_index: ints.sdFnIndex,
        width: ints.sdWidth,
        height: ints.sdHeight,
        steps: ints.sdSteps,
        cfg_scale: ints.sdCfg,
        sampler: form.sdSampler.trim(),
        schedule: form.sdSchedule.trim(),
        negative_prompt: form.sdNegative,
      },
      mood: {
        ...asRecord(raw.mood),
        enabled: form.moodEnabled,
        baseline: ints.moodBaseline,
        delta_threshold: ints.moodDelta,
        min_confidence: ints.moodConfidence,
        daily_cap: ints.moodDailyCap,
        timeline_keep: ints.moodTimeline,
        judge_provider_id: form.moodJudgeProvider.trim(),
        judge_model: form.moodJudgeModel.trim(),
      },
      self_memory: {
        ...asRecord(raw.self_memory),
        enabled: form.selfMemEnabled,
        daily_quota: ints.selfMemQuota,
        recall_topk: ints.selfMemTopk,
        recall_budget: ints.selfMemBudget,
      },
      proactor: {
        ...asRecord(raw.proactor),
        enabled: form.proactorEnabled,
        wake_check_minutes: ints.proactorCheckMinutes,
        wake_prob_percent: ints.proactorWakeProb,
        wake_every_n: ints.proactorWakeEveryN,
        active_hours: ints.proactorActiveHours,
        speakup_peak_percent: ints.proactorPeak,
        speakup_valley_percent: ints.proactorValley,
        self_cooldown_minutes: ints.proactorCooldown,
        quiet_hours: form.proactorQuiet.trim(),
        polls_max_per_session: ints.proactorPollsMax,
      },
      mcp: {
        ...asRecord(raw.mcp),
        enabled: form.mcpEnabled,
        timeout: ints.mcpTimeout,
        call_min_level: ints.mcpMinLevel,
      },
      watchers: {
        ...asRecord(raw.watchers),
        max_per_session: ints.watchersMax,
      },
      ext_trigger: {
        ...asRecord(raw.ext_trigger),
        enabled: form.extEnabled,
        // 留空表示不改动现有令牌（后端保底沿用旧值）
        token: form.extToken,
        cooldown_seconds: ints.extCooldown,
        base_url: form.extBaseUrl.trim(),
      },
    }
    setSaving(true)
    setError('')
    try {
      const config = await putConfig(payload)
      setRaw(config as unknown as RawConfig)
      setForm(toForm(config))
      showToast({ key: 'systemSaved' })
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
          <div className={styles.title}>{t('settingDialogAsideSystemOptionLabel')}</div>
          <div className={styles.subtitle}>{t('systemSubtitle')}</div>
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
          <div className={styles.sectionTitle}>{t('systemTools')}</div>
          <div className={styles.hint}>{t('systemToolsHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresTools')}
              checked={form.toolsEnabled}
              onChange={(checked) => patch({ toolsEnabled: checked })}
            />
            <NumberField
              label={t('featuresMaxIterations')}
              hint={t('systemToolsMaxHint')}
              value={form.toolsMax}
              onChange={(value) => patch({ toolsMax: value })}
            />
            <NumberField
              label={t('featuresTimeout')}
              value={form.toolsTimeout}
              onChange={(value) => patch({ toolsTimeout: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemRoleplay')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresRoleplayFormat')}
              checked={form.rpFormat}
              onChange={(checked) => patch({ rpFormat: checked })}
            />
            <SwitchRow
              label={t('featuresRoleplayAttributes')}
              checked={form.rpAttributes}
              onChange={(checked) => patch({ rpAttributes: checked })}
            />
            <NumberField
              label={t('featuresIntroChars')}
              hint={t('systemCharsHint')}
              value={form.introChars}
              onChange={(value) => patch({ introChars: value })}
            />
            <NumberField
              label={t('featuresBackgroundChars')}
              hint={t('systemCharsHint')}
              value={form.backgroundChars}
              onChange={(value) => patch({ backgroundChars: value })}
            />
            <NumberField
              label={t('featuresWorldChars')}
              hint={t('systemCharsHint')}
              value={form.worldChars}
              onChange={(value) => patch({ worldChars: value })}
            />
            <NumberField
              label={t('featuresMesExampleChars')}
              hint={t('systemCharsHint')}
              value={form.mesExampleChars}
              onChange={(value) => patch({ mesExampleChars: value })}
            />
            <NumberField
              label={t('featuresPinnedChars')}
              hint={t('systemCharsHint')}
              value={form.pinnedChars}
              onChange={(value) => patch({ pinnedChars: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemWebSearch')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresWeb')}
              checked={form.webEnabled}
              onChange={(checked) => patch({ webEnabled: checked })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemWebEngine')}</div>
                <div className={styles.rowDesc}>{t('systemWebEngineDesc')}</div>
              </div>
              <Select<'chromium' | 'http'>
                value={form.webEngine}
                ariaLabel={t('systemWebEngine')}
                options={[
                  { value: 'chromium', label: t('systemWebEngineChromium') },
                  { value: 'http', label: t('systemWebEngineHttp') },
                ]}
                onChange={(value) => patch({ webEngine: value })}
              />
            </div>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemWebSearchEngine')}</div>
                <div className={styles.rowDesc}>{t('systemWebSearchEngineDesc')}</div>
              </div>
              <Select<'bing' | 'baidu'>
                value={form.webSearchEngine}
                ariaLabel={t('systemWebSearchEngine')}
                options={[
                  { value: 'bing', label: 'Bing' },
                  { value: 'baidu', label: t('systemWebSearchBaidu') },
                ]}
                onChange={(value) => patch({ webSearchEngine: value })}
              />
            </div>
            <NumberField
              label={t('systemWebResults')}
              value={form.webResults}
              onChange={(value) => patch({ webResults: value })}
            />
            <NumberField
              label={t('systemWebMaxChars')}
              value={form.webMaxChars}
              onChange={(value) => patch({ webMaxChars: value })}
            />
            <NumberField
              label={t('systemWebIterations')}
              hint={t('systemWebIterationsHint')}
              value={form.webIterations}
              onChange={(value) => patch({ webIterations: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemBrowser')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresBrowser')}
              checked={form.browserEnabled}
              onChange={(checked) => patch({ browserEnabled: checked })}
            />
            <SwitchRow
              label={t('systemBrowserHeadless')}
              desc={t('systemBrowserHeadlessDesc')}
              checked={form.browserHeadless}
              onChange={(checked) => patch({ browserHeadless: checked })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemPixiv')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresPixiv')}
              checked={form.pixivEnabled}
              onChange={(checked) => patch({ pixivEnabled: checked })}
            />
            <SwitchRow
              label={t('systemPixivR18')}
              desc={t('systemPixivR18Desc')}
              checked={form.pixivR18}
              onChange={(checked) => patch({ pixivR18: checked })}
            />
            <NumberField
              label={t('systemPixivCandidates')}
              value={form.pixivCandidates}
              onChange={(value) => patch({ pixivCandidates: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemImage')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresImage')}
              checked={form.imageEnabled}
              onChange={(checked) => patch({ imageEnabled: checked })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemImageSize')}</div>
                <div className={styles.rowDesc}>{t('systemImageSizeDesc')}</div>
              </div>
              <Select
                value={form.imageSize}
                ariaLabel={t('systemImageSize')}
                options={['1K', '2K', '4K'].map((value) => ({ value, label: value }))}
                onChange={(value) => patch({ imageSize: value })}
              />
            </div>
          </div>

          <div className={styles.sectionTitle}>{t('systemSd')}</div>
          <div className={styles.hint}>{t('systemSdHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemSdEnabled')}
              checked={form.sdEnabled}
              onChange={(checked) => patch({ sdEnabled: checked })}
            />
            <TextField
              label={t('systemSdBaseUrl')}
              hint={t('systemSdBaseUrlHint')}
              value={form.sdBaseUrl}
              placeholder="http://127.0.0.1:7860"
              onChange={(value) => patch({ sdBaseUrl: value })}
            />
            <NumberField
              label={t('systemSdFnIndex')}
              hint={t('systemSdFnIndexHint')}
              value={form.sdFnIndex}
              onChange={(value) => patch({ sdFnIndex: value })}
            />
            <NumberField
              label={t('systemSdWidth')}
              value={form.sdWidth}
              onChange={(value) => patch({ sdWidth: value })}
            />
            <NumberField
              label={t('systemSdHeight')}
              value={form.sdHeight}
              onChange={(value) => patch({ sdHeight: value })}
            />
            <NumberField
              label={t('systemSdSteps')}
              value={form.sdSteps}
              onChange={(value) => patch({ sdSteps: value })}
            />
            <NumberField
              label={t('systemSdCfg')}
              value={form.sdCfg}
              onChange={(value) => patch({ sdCfg: value })}
            />
            <TextField
              label={t('systemSdSampler')}
              value={form.sdSampler}
              onChange={(value) => patch({ sdSampler: value })}
            />
            <TextField
              label={t('systemSdSchedule')}
              value={form.sdSchedule}
              onChange={(value) => patch({ sdSchedule: value })}
            />
            <TextField
              label={t('systemSdNegative')}
              value={form.sdNegative}
              onChange={(value) => patch({ sdNegative: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemMood')}</div>
          <div className={styles.hint}>{t('systemMoodHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemMoodEnabled')}
              checked={form.moodEnabled}
              onChange={(checked) => patch({ moodEnabled: checked })}
            />
            <NumberField
              label={t('systemMoodBaseline')}
              hint={t('systemMoodBaselineHint')}
              value={form.moodBaseline}
              onChange={(value) => patch({ moodBaseline: value })}
            />
            <NumberField
              label={t('systemMoodDelta')}
              hint={t('systemMoodDeltaHint')}
              value={form.moodDelta}
              onChange={(value) => patch({ moodDelta: value })}
            />
            <NumberField
              label={t('systemMoodConfidence')}
              value={form.moodConfidence}
              onChange={(value) => patch({ moodConfidence: value })}
            />
            <NumberField
              label={t('systemMoodDailyCap')}
              value={form.moodDailyCap}
              onChange={(value) => patch({ moodDailyCap: value })}
            />
            <NumberField
              label={t('systemMoodTimeline')}
              value={form.moodTimeline}
              onChange={(value) => patch({ moodTimeline: value })}
            />
            <TextField
              label={t('systemMoodJudgeProvider')}
              hint={t('systemMoodJudgeHint')}
              value={form.moodJudgeProvider}
              onChange={(value) => patch({ moodJudgeProvider: value })}
            />
            <TextField
              label={t('systemMoodJudgeModel')}
              value={form.moodJudgeModel}
              onChange={(value) => patch({ moodJudgeModel: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemSelfMemory')}</div>
          <div className={styles.hint}>{t('systemSelfMemoryHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemSelfMemoryEnabled')}
              checked={form.selfMemEnabled}
              onChange={(checked) => patch({ selfMemEnabled: checked })}
            />
            <NumberField
              label={t('systemSelfMemoryQuota')}
              value={form.selfMemQuota}
              onChange={(value) => patch({ selfMemQuota: value })}
            />
            <NumberField
              label={t('systemSelfMemoryTopk')}
              value={form.selfMemTopk}
              onChange={(value) => patch({ selfMemTopk: value })}
            />
            <NumberField
              label={t('systemSelfMemoryBudget')}
              value={form.selfMemBudget}
              onChange={(value) => patch({ selfMemBudget: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemProactor')}</div>
          <div className={styles.hint}>{t('systemProactorHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemProactorEnabled')}
              checked={form.proactorEnabled}
              onChange={(checked) => patch({ proactorEnabled: checked })}
            />
            <NumberField
              label={t('systemProactorCheckMinutes')}
              value={form.proactorCheckMinutes}
              onChange={(value) => patch({ proactorCheckMinutes: value })}
            />
            <NumberField
              label={t('systemProactorWakeProb')}
              value={form.proactorWakeProb}
              onChange={(value) => patch({ proactorWakeProb: value })}
            />
            <NumberField
              label={t('systemProactorWakeEveryN')}
              hint={t('systemProactorWakeEveryNHint')}
              value={form.proactorWakeEveryN}
              onChange={(value) => patch({ proactorWakeEveryN: value })}
            />
            <NumberField
              label={t('systemProactorActiveHours')}
              value={form.proactorActiveHours}
              onChange={(value) => patch({ proactorActiveHours: value })}
            />
            <NumberField
              label={t('systemProactorPeak')}
              value={form.proactorPeak}
              onChange={(value) => patch({ proactorPeak: value })}
            />
            <NumberField
              label={t('systemProactorValley')}
              value={form.proactorValley}
              onChange={(value) => patch({ proactorValley: value })}
            />
            <NumberField
              label={t('systemProactorCooldown')}
              value={form.proactorCooldown}
              onChange={(value) => patch({ proactorCooldown: value })}
            />
            <TextField
              label={t('systemProactorQuiet')}
              hint={t('systemProactorQuietHint')}
              value={form.proactorQuiet}
              placeholder="01:00-08:00"
              onChange={(value) => patch({ proactorQuiet: value })}
            />
            <NumberField
              label={t('systemProactorPollsMax')}
              value={form.proactorPollsMax}
              onChange={(value) => patch({ proactorPollsMax: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemMcp')}</div>
          <div className={styles.hint}>{t('systemMcpHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemMcpEnabled')}
              checked={form.mcpEnabled}
              onChange={(checked) => patch({ mcpEnabled: checked })}
            />
            <NumberField
              label={t('systemMcpTimeout')}
              value={form.mcpTimeout}
              onChange={(value) => patch({ mcpTimeout: value })}
            />
            <NumberField
              label={t('systemMcpMinLevel')}
              hint={t('systemMcpMinLevelHint')}
              value={form.mcpMinLevel}
              onChange={(value) => patch({ mcpMinLevel: value })}
            />
            <NumberField
              label={t('systemWatchersMax')}
              hint={t('systemWatchersMaxHint')}
              value={form.watchersMax}
              onChange={(value) => patch({ watchersMax: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemExtTrigger')}</div>
          <div className={styles.hint}>{t('systemExtTriggerHint')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemExtTriggerEnabled')}
              checked={form.extEnabled}
              onChange={(checked) => patch({ extEnabled: checked })}
            />
            <TextField
              label={t('systemExtTriggerToken')}
              hint={
                form.extTokenSet ? t('systemExtTriggerTokenSet') : t('systemExtTriggerTokenHint')
              }
              value={form.extToken}
              placeholder={form.extTokenSet ? '••••••••' : ''}
              onChange={(value) => patch({ extToken: value })}
            />
            <NumberField
              label={t('systemExtTriggerCooldown')}
              value={form.extCooldown}
              onChange={(value) => patch({ extCooldown: value })}
            />
            <TextField
              label={t('systemExtTriggerBaseUrl')}
              hint={t('systemExtTriggerBaseUrlHint')}
              value={form.extBaseUrl}
              onChange={(value) => patch({ extBaseUrl: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemGalgame')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('featuresGalgame')}
              checked={form.galgameEnabled}
              onChange={(checked) => patch({ galgameEnabled: checked })}
            />
            <NumberField
              label={t('featuresGalgameLoops')}
              value={form.galgameLoops}
              onChange={(value) => patch({ galgameLoops: value })}
            />
          </div>

          <div className={styles.sectionTitle}>{t('systemTts')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemTtsEnabled')}
              checked={form.ttsEnabled}
              onChange={(checked) => patch({ ttsEnabled: checked })}
            />
            <NumberField
              label={t('systemTtsMinLevel')}
              hint={t('systemTtsMinLevelHint')}
              value={form.ttsMinLevel}
              onChange={(value) => patch({ ttsMinLevel: value })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemTtsEngine')}</div>
                <div className={styles.rowDesc}>{t('systemTtsEngineDesc')}</div>
              </div>
              <Select<'deepseek' | 'doubao'>
                value={form.ttsEngine}
                ariaLabel={t('systemTtsEngine')}
                options={[
                  { value: 'deepseek', label: t('systemTtsEngineDeepseek') },
                  { value: 'doubao', label: t('systemTtsEngineDoubao') },
                ]}
                onChange={(value) => patch({ ttsEngine: value })}
              />
            </div>
            <TextField
              label={t('systemTtsApiKey')}
              hint={t('systemTtsApiKeyHint')}
              value={form.ttsApiKey}
              onChange={(value) => patch({ ttsApiKey: value })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemTtsResource')}</div>
              </div>
              <Select
                value={form.ttsResourceId}
                ariaLabel={t('systemTtsResource')}
                options={[
                  { value: 'seed-tts-2.0', label: 'seed-tts-2.0' },
                  { value: 'seed-tts-1.0', label: 'seed-tts-1.0' },
                  { value: 'seed-tts-1.0-concurr', label: 'seed-tts-1.0-concurr' },
                  { value: 'seed-icl-2.0', label: 'seed-icl-2.0' },
                ]}
                onChange={(value) => patch({ ttsResourceId: value })}
              />
            </div>
            <TextField
              label={t('systemTtsDefaultSpeaker')}
              hint={t('systemTtsDefaultSpeakerHint')}
              value={form.ttsDefaultSpeaker}
              placeholder="S_BcqE5org2"
              onChange={(value) => patch({ ttsDefaultSpeaker: value })}
            />
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowLabel}>{t('systemTtsFormat')}</div>
              </div>
              <Select<'mp3' | 'ogg_opus' | 'pcm'>
                value={form.ttsFormat}
                ariaLabel={t('systemTtsFormat')}
                options={[
                  { value: 'mp3', label: 'mp3' },
                  { value: 'ogg_opus', label: 'ogg_opus' },
                  { value: 'pcm', label: 'pcm' },
                ]}
                onChange={(value) => patch({ ttsFormat: value })}
              />
            </div>
            <NumberField
              label={t('systemTtsSampleRate')}
              value={form.ttsSampleRate}
              onChange={(value) => patch({ ttsSampleRate: value })}
            />
            <NumberField
              label={t('systemTtsSpeechRate')}
              value={form.ttsSpeechRate}
              onChange={(value) => patch({ ttsSpeechRate: value })}
            />
            <NumberField
              label={t('systemTtsLoudnessRate')}
              value={form.ttsLoudnessRate}
              onChange={(value) => patch({ ttsLoudnessRate: value })}
            />
            <TextField
              label={t('systemTtsProxy')}
              hint={t('systemTtsProxyHint')}
              value={form.ttsProxy}
              onChange={(value) => patch({ ttsProxy: value })}
            />
            <NumberField
              label={t('systemTtsTimeout')}
              value={form.ttsTimeout}
              onChange={(value) => patch({ ttsTimeout: value })}
            />
          </div>
          <div className={styles.sectionTitle}>{t('systemTtsVoices')}</div>
          <div className={styles.hint}>{t('systemTtsVoicesHint')}</div>
          <div className={styles.rows}>
            {form.ttsVoices.length === 0 ? (
              <div className={styles.hint}>{t('systemTtsVoicesEmpty')}</div>
            ) : (
              form.ttsVoices.map((voice, index) => (
                <div className={styles.row} key={index}>
                  <input
                    className={styles.input}
                    value={voice.name}
                    placeholder={t('systemTtsVoiceName')}
                    onChange={(event) =>
                      patch({
                        ttsVoices: form.ttsVoices.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, name: event.target.value } : item
                        ),
                      })
                    }
                  />
                  <input
                    className={styles.input}
                    value={voice.id}
                    placeholder={t('systemTtsVoiceId')}
                    onChange={(event) =>
                      patch({
                        ttsVoices: form.ttsVoices.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, id: event.target.value } : item
                        ),
                      })
                    }
                  />
                  <Button
                    variant="outlined"
                    size="s"
                    onClick={() =>
                      patch({
                        ttsVoices: form.ttsVoices.filter((_, itemIndex) => itemIndex !== index),
                      })
                    }
                  >
                    {t('systemTtsVoiceRemove')}
                  </Button>
                </div>
              ))
            )}
          </div>
          <div className={styles.actions}>
            <Button
              variant="outlined"
              size="s"
              onClick={() => patch({ ttsVoices: [...form.ttsVoices, { name: '', id: '' }] })}
            >
              {t('systemTtsVoiceAdd')}
            </Button>
          </div>

          <div className={styles.sectionTitle}>{t('systemLoop')}</div>
          <div className={styles.rows}>
            <SwitchRow
              label={t('systemLoopEnabled')}
              desc={t('systemLoopDesc')}
              checked={form.loopEnabled}
              onChange={(checked) => patch({ loopEnabled: checked })}
            />
          </div>
        </>
      )}
    </div>
  )
}