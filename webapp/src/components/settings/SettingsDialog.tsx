import clsx from 'clsx'
import { useRef, useState, type ReactNode } from 'react'
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
import { FlyApiError } from '../../services/flyai/client.js'
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
import { CdkRedeemField } from './CdkRedeemField.js'
import { DeepSeekImportDialog } from './DeepSeekImportDialog.js'
import { InvitePanel } from './InvitePanel.js'
import { closeSettings, useSettingsDialog, type SettingsSection } from './settingsDialogState.js'
import { AccessPanel } from './admin/AccessPanel.js'
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
            <LocaleSetting />
            <TtsVoiceSetting />
            <LocationSetting />
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

