import clsx from 'clsx'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  IconBoltOutline16,
  IconChartOutline16,
  IconCopyOutline16,
  IconDarkOutline16,
  IconDataOutline16,
  IconDownloadOutline16,
  IconFollowsystemOutline16,
  IconLibraryOutline16,
  IconLightOutline16,
  IconLockOutline16,
  IconNoticeOutline16,
  IconPanelLeftOutline16,
  IconSendOutline16,
  IconSettingsOutline16,
  IconThinkOutline16,
  IconLinkOutline16,
  IconUsersOutline16,
} from '../base/icons.js'
import { importSessions, removeAllSessions } from '../../services/chat/store.js'
import { useAuth } from '../../services/flyai/auth.js'
import { assetUrl, FlyApiError } from '../../services/flyai/client.js'
import { uploadImage } from '../../services/flyai/lore.js'
import { compressImageFile, formatSize } from '../../services/attachments.js'
import {
  applyPocOverlay,
  cancelPocUpdate,
  checkPocUpdate,
  downloadPocFullApk,
  downloadPocOverlay,
  getPocInfo,
  isPocUpdateSupported,
  onPocReady,
  onPocUpdateEvent,
  type PocInfo,
  type PocUpdateEvent,
} from '../../services/pocUpdate.js'
import {
  saveDeviceBackground,
  setLocalBackground,
  useBackground,
} from '../../services/background.js'
import { deviceKind, type DeviceKind } from '../../services/device.js'
import type { BackgroundMode } from '../../services/flyai/admin.js'
import {
  parseDeepSeekExport,
  type ParsedDeepSeekExport,
} from '../../services/flyai/deepseekExport.js'
import { regionCodes, getRegionName, provinces } from '../../services/location.js'
import {
  isChineseBrowser,
  setLocale,
  setLocation,
  setThemePreference,
  setTtsVoice,
  useSettings,
  type ThemePreference,
} from '../../services/settings.js'
import { useTranslate, type TranslationKey, type TranslationMessage } from '../../i18n/index.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { Select } from '../base/Select.js'
import { showToast } from '../base/toastState.js'
import { AfdianClaimCard } from './AfdianClaimCard.js'
import { CdkRedeemField } from './CdkRedeemField.js'
import { DeepSeekImportDialog } from './DeepSeekImportDialog.js'
import { InvitePanel } from './InvitePanel.js'
import { closeSettings, useSettingsDialog, type SettingsSection } from './settingsDialogState.js'
import { AccessPanel } from './admin/AccessPanel.js'
import { AfdianPanel } from './admin/AfdianPanel.js'
import { AnnouncementPanel } from './admin/AnnouncementPanel.js'
import { CdkPanel } from './admin/CdkPanel.js'
import { DeepSeekPanel } from './admin/DeepSeekPanel.js'
import { EmailPanel } from './admin/EmailPanel.js'
import { FeaturesPanel } from './admin/FeaturesPanel.js'
import { IntegrationPanel } from './admin/IntegrationPanel.js'
import { MemoryPanel } from './admin/MemoryPanel.js'
import { ModelsPanel } from './admin/ModelsPanel.js'
import { SystemPanel } from './admin/SystemPanel.js'
import { UsagePanel } from './admin/UsagePanel.js'
import { UsersPanel } from './admin/UsersPanel.js'
import styles from './Settings.module.css'

/**
 * 设置弹窗：通用 / 用量 / 模型 / 功能 / 数据，管理员另有用户与系统。
 * 桌面端为居中弹窗、左侧导航；移动端为底部弹层、导航横排在内容上方
 */
export const SettingsDialog = () => {
  const open = useSettingsDialog((state) => state.open)
  return open ? <SettingsContent /> : null
}

const sections: {
  value: SettingsSection
  label: TranslationKey
  icon: ReactNode
  adminOnly?: boolean
}[] = [
  {
    value: 'general',
    label: 'settingDialogAsideGeneralOptionLabel',
    icon: <IconSettingsOutline16 />,
  },
  {
    value: 'usage',
    label: 'settingDialogAsideUsageOptionLabel',
    icon: <IconChartOutline16 />,
  },
  {
    value: 'models',
    label: 'settingDialogAsideModelsOptionLabel',
    icon: <IconLibraryOutline16 />,
  },
  {
    value: 'features',
    label: 'settingDialogAsideFeaturesOptionLabel',
    icon: <IconDataOutline16 />,
  },
  {
    value: 'memory',
    label: 'settingDialogAsideMemoryOptionLabel',
    icon: <IconThinkOutline16 />,
  },
  {
    value: 'data',
    label: 'settingDialogAsideDataOptionLabel',
    icon: <IconDownloadOutline16 />,
  },
  {
    value: 'invite',
    label: 'settingDialogAsideInviteOptionLabel',
    icon: <IconUsersOutline16 />,
  },
  {
    value: 'users',
    label: 'settingDialogAsideUsersOptionLabel',
    icon: <IconUsersOutline16 />,
    adminOnly: true,
  },
  {
    value: 'system',
    label: 'settingDialogAsideSystemOptionLabel',
    icon: <IconPanelLeftOutline16 />,
    adminOnly: true,
  },
  {
    value: 'integration',
    label: 'settingDialogAsideIntegrationOptionLabel',
    icon: <IconLinkOutline16 />,
    adminOnly: true,
  },
  {
    value: 'cdk',
    label: 'settingDialogAsideCdkOptionLabel',
    icon: <IconCopyOutline16 />,
    adminOnly: true,
  },
  {
    value: 'afdian',
    label: 'settingDialogAsideAfdianOptionLabel',
    icon: <IconBoltOutline16 />,
    adminOnly: true,
  },
  {
    value: 'access',
    label: 'settingDialogAsideAccessOptionLabel',
    icon: <IconLockOutline16 />,
    adminOnly: true,
  },
  {
    value: 'deepseek',
    label: 'settingDialogAsideDeepseekOptionLabel',
    icon: <IconBoltOutline16 />,
    adminOnly: true,
  },
  {
    value: 'email',
    label: 'settingDialogAsideEmailOptionLabel',
    icon: <IconSendOutline16 />,
    adminOnly: true,
  },
  {
    value: 'announcement',
    label: 'settingDialogAsideAnnouncementOptionLabel',
    icon: <IconNoticeOutline16 />,
    adminOnly: true,
  },
]

const SettingsContent = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  // 打开时按调用方指定的分组选中，之后在弹窗内切换
  const [section, setSection] = useState(() => useSettingsDialog.getState().section)
  const historyDelete = useHistoryDelete()

  const visibleSections = sections.filter((item) => !item.adminOnly || isAdmin)
  // 非管理员被指定到管理员分组时退回「通用」
  const active = visibleSections.some((item) => item.value === section) ? section : 'general'

  const renderData = () => (
    <>
      <AfdianClaimCard />
      <CdkRedeemField />
      <ImportConversations />
      <SettingCell
        label={t('deleteAllSessions')}
        description={t('deleteAllSessionsDescription')}
        status={null}
        action={
          <Button
            variant="dangerOutlined"
            size="m"
            className={styles.cellButton}
            disabled={historyDelete.busy}
            onClick={historyDelete.open}
          >
            {t('delete')}
          </Button>
        }
      />
    </>
  )

  const renderSection = () => {
    switch (active) {
      case 'usage':
        return <UsagePanel />
      case 'models':
        return <ModelsPanel />
      case 'features':
        return <FeaturesPanel />
      case 'memory':
        return <MemoryPanel />
      case 'integration':
        return isAdmin ? <IntegrationPanel /> : null
      case 'cdk':
        return isAdmin ? <CdkPanel /> : null
      case 'afdian':
        return isAdmin ? <AfdianPanel /> : null
      case 'access':
        return isAdmin ? <AccessPanel /> : null
      case 'deepseek':
        return isAdmin ? <DeepSeekPanel /> : null
      case 'email':
        return isAdmin ? <EmailPanel /> : null
      case 'announcement':
        return isAdmin ? <AnnouncementPanel /> : null
      case 'users':
        return isAdmin ? <UsersPanel /> : null
      case 'system':
        return isAdmin ? <SystemPanel /> : null
      case 'data':
        return renderData()
      case 'invite':
        return <InvitePanel />
      default:
        return (
          <>
            <ThemeSetting />
            <BackgroundSetting />
            <LocaleSetting />
            <TtsVoiceSetting />
            <LocationSetting />
            <GitHubSetting />
            <PocUpdateSetting />
          </>
        )
    }
  }

  return (
    <>
      <Modal
        title={t('settings')}
        closable
        className={styles.dialog}
        mainClassName={styles.dialogMain}
        onClose={() => {
          if (!historyDelete.busy) {
            closeSettings()
          }
        }}
      >
        <div role="tablist" className={styles.navigation}>
          {visibleSections.map((item) => (
            <button
              key={item.value}
              type="button"
              role="tab"
              aria-selected={active === item.value}
              className={clsx(styles.navItem, active === item.value && styles.navItemActive)}
              onClick={() => setSection(item.value)}
            >
              {item.icon}
              {t(item.label)}
            </button>
          ))}
        </div>
        <div role="tabpanel" className={styles.content}>
          {renderSection()}
        </div>
      </Modal>
      {historyDelete.opened ? <DeleteAllConfirm historyDelete={historyDelete} /> : null}
    </>
  )
}

/** 导入 FlyAI JSON/ZIP、DeepSeek 导出或 Chatbox 备份；导入完成后列表自动刷新 */
const ImportConversations = () => {
  const t = useTranslate()
  const [busy, setBusy] = useState(false)
  const [archive, setArchive] = useState<ParsedDeepSeekExport | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <>
      <SettingCell
        label={t('importConversations')}
        description={t('importConversationsDescription')}
        status={null}
        action={
          <>
            <input
              ref={inputRef}
              type="file"
              accept=".json,.zip,.md,.markdown,application/json,application/zip,text/markdown"
              hidden
              onChange={async (event) => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (!file || busy) {
                  return
                }
                setBusy(true)
                try {
                  // DeepSeek 数据导出包在前端解析，挑选会话后走普通导入接口
                  if (file.name.toLowerCase().endsWith('.zip')) {
                    const parsed = parseDeepSeekExport(
                      new Uint8Array(await file.arrayBuffer())
                    )
                    if (parsed) {
                      if (parsed.sessions.length === 0) {
                        showToast({ key: 'deepSeekImportEmpty', type: 'error' })
                      } else {
                        setArchive(parsed)
                      }
                      return
                    }
                  }
                  const count = await importSessions(file)
                  showToast({ key: 'importedConversations', params: { count } })
                } catch (error) {
                  const message =
                    error instanceof FlyApiError
                      ? error.message
                      : error instanceof Error
                        ? error.message
                        : t('unknownError')
                  showToast({
                    key: 'importConversationsFailed',
                    params: { message },
                    type: 'error',
                  })
                } finally {
                  setBusy(false)
                }
              }}
            />
            <Button
              variant="outlined"
              size="m"
              className={styles.cellButton}
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              {t(busy ? 'importingConversations' : 'upload')}
            </Button>
          </>
        }
      />
      {archive ? (
        <DeepSeekImportDialog parsed={archive} onClose={() => setArchive(null)} />
      ) : null}
    </>
  )
}

/** 删除确认保留在设置之上；失败可重试，成功只关闭确认弹窗。 */
const DeleteAllConfirm = ({
  historyDelete,
}: {
  historyDelete: ReturnType<typeof useHistoryDelete>
}) => {
  const t = useTranslate()
  return (
    <Modal
      title={t('deleteAllSessionsModalTitle')}
      className={styles.deleteConfirm}
      onClose={historyDelete.cancel}
      footer={
        <div className={styles.deleteActions} aria-busy={historyDelete.busy}>
          <Button
            variant="outlined"
            autoFocus
            disabled={historyDelete.busy}
            onClick={historyDelete.cancel}
          >
            {t('cancel')}
          </Button>
          <Button
            variant="danger"
            disabled={historyDelete.busy}
            onClick={() => void historyDelete.confirm()}
          >
            {t(historyDelete.busy ? 'historyDeleting' : 'deleteAllSessions')}
          </Button>
        </div>
      }
    >
      {t('deleteAllSessionsModalDescription')}
    </Modal>
  )
}

const useHistoryDelete = () => {
  const [opened, setOpened] = useState(false)
  const [busy, setBusy] = useState(false)
  const deleting = useRef(false)

  const confirm = async () => {
    if (deleting.current) {
      return
    }
    deleting.current = true
    setBusy(true)
    try {
      if (await removeAllSessions()) {
        setOpened(false)
        showToast({ key: 'historyDeleted' })
      }
    } catch {
      showToast({ key: 'historyDeleteFailed', type: 'error' })
    } finally {
      deleting.current = false
      setBusy(false)
    }
  }

  return {
    opened,
    busy,
    open: () => setOpened(true),
    cancel: () => {
      if (!deleting.current) {
        setOpened(false)
      }
    },
    confirm,
  }
}

const LocationSetting = () => {
  const t = useTranslate()
  const locale = useSettings((state) => state.locale)
  const location = useSettings((state) => state.location)
  const province =
    location?.region === 'CN'
      ? provinces.find((item) => item.code === location.province)
      : undefined

  return (
    <SettingCell
      label={t('settingsLocation')}
      status={null}
      className={styles.locationCell}
      action={
        <div className={styles.locationSelectors}>
          {isChineseBrowser() ? (
            <>
              <Select
                ariaLabel={t('settingsLocationProvince')}
                className={styles.provinceSelect}
                value={province?.code ?? ''}
                placeholder={t('settingsLocationProvince')}
                options={provinces.map((item) => ({ value: item.code, label: item.name }))}
                onChange={(value) => {
                  if (value !== province?.code) {
                    setLocation({ region: 'CN', province: value })
                  }
                }}
              />
              <Select
                ariaLabel={t('settingsLocationCity')}
                className={styles.citySelect}
                value={province ? (location?.city ?? '') : ''}
                disabled={!province?.cities.length}
                placeholder={t('settingsLocationCity')}
                options={
                  province?.cities.map((item) => ({ value: item.code, label: item.name })) ?? []
                }
                onChange={(value) => {
                  if (province) {
                    setLocation({ region: 'CN', province: province.code, city: value })
                  }
                }}
              />
            </>
          ) : (
            <Select
              ariaLabel={t('settingsLocationRegion')}
              className={styles.regionSelect}
              value={location?.region ?? ''}
              placeholder={t('settingsLocationRegion')}
              options={regionCodes
                .map((code) => ({ value: code, label: getRegionName(code, locale) }))
                .sort((a, b) => a.label.localeCompare(b.label, locale))}
              onChange={(value) => setLocation({ region: value })}
            />
          )}
        </div>
      }
    />
  )
}

const GITHUB_URL = 'https://github.com/Artificialheaven/flyai'

/** 通用设置最底部的项目入口：网页端新标签打开，POC/APK 由原生层转系统浏览器 */
const GitHubSetting = () => {
  const t = useTranslate()
  return (
    <SettingCell
      label={t('settingsGitHub')}
      description={t('settingsGitHubDescription')}
      status={null}
      action={
        <a
          className={styles.linkButton}
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          <IconLinkOutline16 />
          {t('settingsGitHubOpen')}
        </a>
      }
    />
  )
}

/** POC 本地更新：只在原生 WebView 注入 window.__flyaiPoc 后显示。 */
const PocUpdateSetting = () => {
  const t = useTranslate()
  const [info, setInfo] = useState<PocInfo | null>(null)
  const [event, setEvent] = useState<PocUpdateEvent | null>(null)
  const [supported, setSupported] = useState(false)

  useEffect(() => {
    const refresh = () => {
      setSupported(isPocUpdateSupported())
      setInfo(getPocInfo())
    }
    refresh()
    const offReady = onPocReady(() => {
      refresh()
      if (isPocUpdateSupported()) {
        checkPocUpdate()
      }
    })
    const offEvent = onPocUpdateEvent((next) => {
      setEvent(next)
      if (next.state === 'staged' || next.state === 'applying') {
        refresh()
      }
    })
    if (isPocUpdateSupported()) {
      checkPocUpdate()
    }
    return () => {
      offReady()
      offEvent()
    }
  }, [])

  if (!supported) {
    return null
  }

  const state = event?.state ?? (info?.pending ? 'staged' : 'idle')
  const current = info
    ? t('pocUpdateCurrent', {
        version: info.versionName || String(info.versionCode),
        rev: info.codeRev || '—',
      }) + (info.overlayVersion ? ` · overlay ${info.overlayVersion}` : '')
    : ''
  let description = current
  if (state === 'checking') {
    description = t('pocUpdateChecking')
  } else if (state === 'available') {
    const size = event?.size ? ` · ${formatSize(event.size)}` : ''
    description = event?.kind === 'full'
      ? `${t('pocUpdateFull')}${size}`
      : `${t('pocUpdateAvailable', { version: event?.version || info?.versionName || '' })}${size}`
  } else if (state === 'downloading') {
    const percent = event?.percent ?? 0
    const loaded = event?.loaded ? formatSize(event.loaded) : ''
    description = `${t('pocUpdateDownloading', { percent })}${loaded ? ` · ${loaded}` : ''}`
  } else if (state === 'staged') {
    description = `${t('pocUpdateStaged')}${t('pocUpdateRestartHint')}`
  } else if (state === 'installing') {
    description = t('pocUpdateInstalling')
  } else if (state === 'applying') {
    description = t('pocUpdateApplying')
  } else if (state === 'uptodate') {
    description = `${t('pocUpdateUpToDate')} · ${current}`
  } else if (state === 'failed') {
    description = event?.error || t('pocUpdateFailed')
  }

  const run = (action: () => { ok?: boolean; error?: string }) => {
    const result = action()
    if (!result.ok) {
      showToast(result.error || t('pocUpdateFailed'))
    }
  }

  let action = (
    <Button
      variant="outlined"
      size="m"
      className={styles.cellButton}
      onClick={() => run(checkPocUpdate)}
    >
      {t('pocUpdateCheck')}
    </Button>
  )
  if (state === 'available') {
    action = (
      <Button
        variant="outlined"
        size="m"
        className={styles.cellButton}
        onClick={() => run(event?.kind === 'full' ? downloadPocFullApk : downloadPocOverlay)}
      >
        {event?.kind === 'full' ? t('pocUpdateFullDownload') : t('pocUpdateDownload')}
      </Button>
    )
  } else if (state === 'downloading') {
    action = (
      <Button
        variant="outlined"
        size="m"
        className={styles.cellButton}
        onClick={() => run(cancelPocUpdate)}
      >
        {t('pocUpdateCancel')}
      </Button>
    )
  } else if (state === 'staged') {
    action = (
      <Button
        size="m"
        className={styles.cellButton}
        onClick={() => run(applyPocOverlay)}
      >
        {t('pocUpdateApplyNow')}
      </Button>
    )
  }

  return (
    <SettingCell
      label={t('pocUpdateTitle')}
      description={description}
      status={null}
      action={action}
    />
  )
}

const LocaleSetting = () => {
  const t = useTranslate()
  const locale = useSettings((state) => state.locale)
  return (
    <SettingCell
      label={t('settingsLocalePreference')}
      status={null}
      action={
        <Select
          ariaLabel={t('settingsLocalePreference')}
          value={locale}
          options={[
            { value: 'zh-CN', label: t('settingsLocaleZhCN'), lang: 'zh-CN' },
            { value: 'en-US', label: t('settingsLocaleEnUS'), lang: 'en' },
          ]}
          onChange={setLocale}
        />
      }
    />
  )
}

/** 朗读音色：stella（海星，女声）/ tide（暗潮，男声） */
const TtsVoiceSetting = () => {
  const t = useTranslate()
  const ttsVoice = useSettings((state) => state.ttsVoice)
  return (
    <SettingCell
      label={t('settingsTtsVoice')}
      status={null}
      action={
        <Select
          ariaLabel={t('settingsTtsVoice')}
          value={ttsVoice}
          options={[
            { value: 'stella', label: t('settingsTtsVoiceStella') },
            { value: 'tide', label: t('settingsTtsVoiceTide') },
          ]}
          onChange={setTtsVoice}
        />
      }
    />
  )
}


const themeOptions: { value: ThemePreference; label: TranslationKey; icon: ReactNode }[] = [
  {
    value: 'light',
    label: 'avatarMenuSettingsDialogThemeSelectLightOption',
    icon: <IconLightOutline16 />,
  },
  {
    value: 'dark',
    label: 'avatarMenuSettingsDialogThemeSelectDarkOption',
    icon: <IconDarkOutline16 />,
  },
  {
    value: 'system',
    label: 'avatarMenuSettingsDialogThemeSelectSystemOption',
    icon: <IconFollowsystemOutline16 />,
  },
]

/**
 * 主题项：标题下方浅色 / 深色 / 跟随系统三个选块。
 * 与下方语言选项连续排列。
 */
const ThemeSetting = () => {
  const t = useTranslate()
  const themePreference = useSettings((state) => state.themePreference)
  const label = t('avatarMenuSettingsDialogThemeItemLabel')
  return (
    <div className={styles.theme}>
      {label}
      <div role="radiogroup" aria-label={label} className={styles.themeOptions}>
        {themeOptions.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={themePreference === option.value}
            className={clsx(
              styles.themeOption,
              themePreference === option.value && styles.themeOptionSelected
            )}
            onClick={() => setThemePreference(option.value)}
          >
            {option.icon}
            {t(option.label)}
          </button>
        ))}
      </div>
    </div>
  )
}

const HEX_RE = /^#[0-9a-fA-F]{6}$/
/** 源文件上限：超过直接拒绝，不做压缩 */
const BG_SOURCE_LIMIT = 20 * 1024 * 1024
/** 压缩目标：长边 2560、文件 ≤3MB（服务端上传上限 5MB） */
const BG_MAX_EDGE = 2560
const BG_TARGET_BYTES = 3 * 1024 * 1024
const BG_COLOR_SAVE_DELAY = 500

const backgroundDevices: { value: DeviceKind; label: TranslationKey }[] = [
  { value: 'desktop', label: 'backgroundDeviceDesktop' },
  { value: 'mobile', label: 'backgroundDeviceMobile' },
]

const backgroundModes: { value: BackgroundMode; label: TranslationKey }[] = [
  { value: 'theme', label: 'backgroundModeTheme' },
  { value: 'solid', label: 'backgroundModeSolid' },
  { value: 'image', label: 'backgroundModeImage' },
]

const defaultSolidColor = () =>
  typeof document !== 'undefined' && document.body.classList.contains('dark')
    ? '#1a1a1c'
    : '#f2f3f5'

type BgSaveStatus = 'idle' | 'saving' | 'saved' | 'error'
type BgPatch = {
  mode?: BackgroundMode
  color?: string
  image?: string
  opacity?: number
}

/**
 * 应用背景：电脑/手机各一套，云端随账号同步。
 * 颜色改动即时预览、500ms 防抖保存；图片先压缩（长边 2560 / ≤3MB）再上传。
 */
const BackgroundSetting = () => {
  const t = useTranslate()
  // 默认展示当前设备，另一台可手动切换
  const [device, setDevice] = useState<DeviceKind>(deviceKind)
  const desktop = useBackground((state) => state.desktop)
  const mobile = useBackground((state) => state.mobile)
  const active = device === 'desktop' ? desktop : mobile

  const [status, setStatus] = useState<BgSaveStatus>('idle')
  const [busy, setBusy] = useState<'compress' | 'upload' | null>(null)
  const [error, setError] = useState<TranslationKey | null>(null)
  const [hexDraft, setHexDraft] = useState(active.color)
  const inputRef = useRef<HTMLInputElement>(null)
  const timerRef = useRef<number | null>(null)
  const pendingRef = useRef<{ device: DeviceKind; patch: BgPatch } | null>(null)
  const saveSeq = useRef(0)

  // 切换设备或服务端校准后同步颜色草稿，并清掉上一台设备的状态
  useEffect(() => {
    setHexDraft(active.color)
    setStatus('idle')
    setError(null)
  }, [device, active.color])

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      // 面板关闭时把还在防抖中的改动补发，避免只预览生效但没落库
      const pending = pendingRef.current
      if (pending) {
        pendingRef.current = null
        void saveDeviceBackground(pending.device, pending.patch).catch(() => {})
      }
    },
    []
  )

  const persist = async (patch: BgPatch) => {
    const seq = ++saveSeq.current
    setStatus('saving')
    setError(null)
    try {
      await saveDeviceBackground(device, patch)
      if (seq === saveSeq.current) {
        setStatus('saved')
      }
    } catch {
      if (seq === saveSeq.current) {
        setStatus('error')
      }
    }
  }

  // 取色器拖动/透明度滑动的防抖保存：即时预览，停手后才打接口
  const scheduleSave = (patch: BgPatch) => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    pendingRef.current = { device, patch }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      pendingRef.current = null
      void persist(patch)
    }, BG_COLOR_SAVE_DELAY)
  }

  const chooseMode = (mode: BackgroundMode) => {
    if (mode === active.mode) {
      return
    }
    setError(null)
    if (mode === 'image' && !active.image) {
      // 还没图：直接打开文件选择，选完自动切到图片模式
      inputRef.current?.click()
      return
    }
    if (mode === 'solid' && !active.color) {
      const color = defaultSolidColor()
      setHexDraft(color)
      setLocalBackground(device, { mode, color })
      void persist({ mode, color })
      return
    }
    setLocalBackground(device, { mode })
    void persist({ mode })
  }

  const commitHex = () => {
    const value = hexDraft.trim().toLowerCase()
    if (!HEX_RE.test(value)) {
      setHexDraft(active.color)
      setError('backgroundColorInvalid')
      return
    }
    setError(null)
    if (value === active.color) {
      return
    }
    setHexDraft(value)
    setLocalBackground(device, { color: value })
    void persist({ color: value })
  }

  const handleFile = async (file: File | undefined) => {
    if (!file) {
      return
    }
    setError(null)
    setStatus('idle')
    if (file.size > BG_SOURCE_LIMIT) {
      setError('backgroundFileTooLarge')
      return
    }
    setBusy('compress')
    let compressed: File
    try {
      compressed = await compressImageFile(file, {
        maxEdge: BG_MAX_EDGE,
        targetBytes: BG_TARGET_BYTES,
        force: true,
        // AI 生成的透明背景（PNG/WebP）保留 alpha，不要被 JPEG 白底吃掉
        preserveAlpha: true,
      })
    } catch {
      setBusy(null)
      setError('backgroundDecodeFailed')
      return
    }
    setBusy('upload')
    try {
      const url = await uploadImage(compressed)
      setBusy(null)
      // 透明图不保留底色，否则 alpha 区域会露出上一次选的纯色
      const transparent = /\.(?:png|webp)$/i.test(url)
      const patch: BgPatch = transparent
        ? { mode: 'image', image: url, color: '' }
        : { mode: 'image', image: url }
      setLocalBackground(device, patch)
      await persist(patch)
    } catch {
      setBusy(null)
      setStatus('error')
      setError('backgroundUploadFailed')
    }
  }

  const removeImage = () => {
    setError(null)
    setLocalBackground(device, { mode: 'theme', image: '' })
    void persist({ mode: 'theme', image: '' })
  }

  const reset = () => {
    setError(null)
    setHexDraft('')
    setLocalBackground(device, {
      mode: 'theme', color: '', image: '', opacity: 100,
    })
    void persist({ mode: 'theme', color: '', image: '', opacity: 100 })
  }

  const statusError = error !== null || status === 'error'
  const statusText =
    busy === 'compress'
      ? t('backgroundCompressing')
      : busy === 'upload'
        ? t('backgroundUploading')
        : error !== null
          ? t(error)
          : status === 'saving'
            ? t('backgroundSaving')
            : status === 'saved'
              ? t('backgroundSaved')
              : status === 'error'
                ? t('backgroundSaveFailed')
                : null

  return (
    <div className={styles.bg}>
      <div className={styles.bgHeader}>
        <div className={styles.bgTitle}>{t('backgroundTitle')}</div>
        <div role="tablist" aria-label={t('backgroundTitle')} className={styles.bgDevices}>
          {backgroundDevices.map((item) => (
            <button
              key={item.value}
              type="button"
              role="tab"
              aria-selected={device === item.value}
              className={clsx(
                styles.bgDevice,
                device === item.value && styles.bgDeviceActive
              )}
              disabled={busy !== null}
              onClick={() => setDevice(item.value)}
            >
              {t(item.label)}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.description}>{t('backgroundHint')}</div>

      <div role="radiogroup" aria-label={t('backgroundTitle')} className={styles.bgModes}>
        {backgroundModes.map((mode) => (
          <button
            key={mode.value}
            type="button"
            role="radio"
            aria-checked={active.mode === mode.value}
            className={clsx(
              styles.bgMode,
              active.mode === mode.value && styles.bgModeSelected
            )}
            disabled={busy !== null}
            onClick={() => chooseMode(mode.value)}
          >
            {t(mode.label)}
          </button>
        ))}
      </div>

      {active.mode !== 'theme' ? (
        <div className={styles.bgOpacityRow}>
          <span className={styles.bgOpacityLabel}>{t('backgroundOpacity')}</span>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            className={styles.bgOpacityRange}
            aria-label={t('backgroundOpacity')}
            value={active.opacity}
            disabled={busy !== null}
            onChange={(event) => {
              const opacity = Number(event.target.value)
              // 即时预览，停手 500ms 后保存
              setLocalBackground(device, { opacity })
              scheduleSave({ opacity })
            }}
          />
          <span className={styles.bgOpacityValue}>{active.opacity}%</span>
        </div>
      ) : null}

      {active.mode === 'solid' ? (
        <div className={styles.bgColorRow}>
          <input
            type="color"
            className={styles.bgColorInput}
            aria-label={t('backgroundColor')}
            value={HEX_RE.test(active.color) ? active.color : '#f2f3f5'}
            disabled={busy !== null}
            onChange={(event) => {
              const color = event.target.value.toLowerCase()
              setError(null)
              setHexDraft(color)
              // 即时预览，保存走防抖；拖动取色器时不会每帧打接口
              setLocalBackground(device, { color })
              scheduleSave({ color })
            }}
          />
          <input
            type="text"
            className={styles.bgHex}
            aria-label={t('backgroundColor')}
            value={hexDraft}
            maxLength={7}
            spellCheck={false}
            placeholder="#rrggbb"
            disabled={busy !== null}
            onChange={(event) => setHexDraft(event.target.value)}
            onBlur={commitHex}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.currentTarget.blur()
              }
            }}
          />
        </div>
      ) : null}

      {active.mode === 'image' ? (
        <div className={styles.bgImageRow}>
          {active.image ? (
            <img
              className={styles.bgThumb}
              src={assetUrl(active.image)}
              alt={t('backgroundImageAlt')}
            />
          ) : null}
          <Button
            variant="outlined"
            size="s"
            disabled={busy !== null}
            onClick={() => inputRef.current?.click()}
          >
            {active.image ? t('backgroundReplaceImage') : t('backgroundPickImage')}
          </Button>
          {active.image ? (
            <Button
              variant="outlined"
              size="s"
              disabled={busy !== null}
              onClick={removeImage}
            >
              {t('backgroundRemoveImage')}
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className={styles.bgFooter}>
        <div
          className={clsx(styles.description, statusError && styles.error)}
          role="status"
          aria-live="polite"
        >
          {statusText ?? t('backgroundSyncHint')}
        </div>
        <button
          type="button"
          className={styles.bgReset}
          disabled={busy !== null}
          onClick={reset}
        >
          {t('backgroundReset')}
        </button>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          // 清空 value：同一张图改过后还能再次选择
          event.target.value = ''
          void handleFile(file)
        }}
      />
    </div>
  )
}

/** `reason` 为失败原因的文案 key，展示时按当前语言翻译后填入 `message` 参数。 */
type Status = (TranslationMessage & { error?: boolean; reason?: TranslationKey }) | null


const SettingCell = ({
  label,
  description,
  status,
  action,
  className,
}: {
  label: string
  description?: string
  status: Status
  action: ReactNode
  className?: string
}) => {
  const t = useTranslate()
  return (
    <div className={clsx(styles.cell, styles.row, className)}>
      <div className={styles.text}>
        <div className={styles.label}>{label}</div>
        {status || description ? (
          <div className={clsx(styles.description, status?.error && styles.error)}>
            {status
              ? t(status.key, status.reason ? { message: t(status.reason) } : status.params)
              : description}
          </div>
        ) : null}
      </div>
      {action}
    </div>
  )
}

