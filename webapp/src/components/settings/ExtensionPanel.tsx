import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { Button } from '../base/Button.js'
import { showToast } from '../base/toastState.js'
import { useTranslate } from '../../i18n/index.js'
import type { ExtensionDetail, ExtensionPanel as ExtensionPanelInfo } from '../../services/extensions/types.js'
import * as client from '../../services/extensions/client.js'
import { useExtensions } from '../../services/extensions/store.js'
import { useExtensionErrors } from '../../services/extensions/widgets.js'
import { SchemaSettingsForm } from './SchemaSettingsForm.js'
import { ExtensionPanelDialog } from './ExtensionPanelDialog.js'
import styles from './ExtensionPanel.module.css'

const levelClass = (level: string): string =>
  level === 'L1' ? styles.badgeOk : level === 'L2' ? styles.badgeWarn : styles.badgeBad

/** 设置 → 扩展：安装/启停/顺序/兼容报告/设置（仅管理员可安装） */
export const ExtensionPanel = () => {
  const t = useTranslate()
  const { items, admin, featureEnabled, loading, error, load, toggle, remove, move, install } =
    useExtensions()
  const runtimeErrors = useExtensionErrors((state) => state.errors)
  const [detail, setDetail] = useState<ExtensionDetail | null>(null)
  const [detailName, setDetailName] = useState('')
  const [panelInfo, setPanelInfo] = useState<ExtensionPanelInfo | null>(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [settingsDraft, setSettingsDraft] = useState('')
  const [settingsValue, setSettingsValue] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState('')
  const [percent, setPercent] = useState(0)
  const installRef = useRef<HTMLInputElement>(null)
  const updateRef = useRef<HTMLInputElement>(null)
  const [updateTarget, setUpdateTarget] = useState('')

  useEffect(() => {
    void load()
  }, [load])

  const openDetail = async (name: string) => {
    if (detailName === name) {
      setDetailName('')
      setDetail(null)
      return
    }
    try {
      const [data, panel] = await Promise.all([
        client.getExtension(name),
        client.getExtensionPanel(name).catch(() => null),
      ])
      setDetail(data)
      setDetailName(name)
      setPanelInfo(panel)
      setSettingsValue((data.settings || {}) as Record<string, unknown>)
      setSettingsDraft(JSON.stringify(data.settings || {}, null, 2))
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err))
    }
  }

  const upload = async (file: File, target?: string) => {
    setBusy(target ? `update:${target}` : 'install')
    setPercent(0)
    try {
      await install(file, {
        update: target,
        onProgress: (value) => setPercent(Math.round(value * 100)),
      })
      showToast({ key: 'extensionsInstalled' })
      if (target) {
        setDetailName('')
        setDetail(null)
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy('')
      setPercent(0)
    }
  }

  const saveSettings = async (name: string) => {
    try {
      const parsed = JSON.parse(settingsDraft || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(t('extensionsSettingsInvalid'))
      }
      await client.putExtensionSettings(name, parsed)
      showToast({ key: 'saved' })
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err))
    }
  }

  if (!featureEnabled) {
    return <div className={styles.empty}>{t('extensionsDisabled')}</div>
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('extensionsTitle')}</div>
          <div className={styles.sub}>{t('extensionsHint')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" onClick={() => void load()} disabled={loading}>
            {t('refresh')}
          </Button>
          {admin ? (
            <Button size="s" onClick={() => installRef.current?.click()} disabled={!!busy}>
              {busy === 'install' ? `${t('extensionsInstalling')} ${percent}%` : t('extensionsInstall')}
            </Button>
          ) : null}
        </div>
      </div>

      <input
        ref={installRef}
        type="file"
        accept=".zip,application/zip"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) void upload(file)
        }}
      />
      <input
        ref={updateRef}
        type="file"
        accept=".zip,application/zip"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file && updateTarget) void upload(file, updateTarget)
        }}
      />

      {busy && percent > 0 ? (
        <div className={styles.progress}>
          <div className={styles.progressFill} style={{ width: `${percent}%` }} />
        </div>
      ) : null}
      {error ? <div className={styles.error}>{error}</div> : null}

      {items.length === 0 && !loading ? (
        <div className={styles.empty}>{t('extensionsEmpty')}</div>
      ) : (
        <div className={styles.list}>
          {items.map((item) => (
            <div className={styles.card} key={item.name}>
              <div className={styles.row}>
                <span className={styles.name}>{item.display_name}</span>
                <span className={styles.meta}>
                  v{item.version} · {item.name}
                </span>
                <span className={clsx(styles.badge, levelClass(item.compat_level))}>
                  {item.compat_level}
                </span>
                {item.missing_modules + item.missing_symbols > 0 ? (
                  <span className={styles.meta}>
                    {t('extensionsGaps', {
                      modules: item.missing_modules,
                      symbols: item.missing_symbols,
                    })}
                  </span>
                ) : null}
                {!item.loadable ? (
                  <span className={clsx(styles.badge, styles.badgeBad)}>
                    {t('extensionsUnsupported')}
                  </span>
                ) : null}
                <div className={styles.actions}>
                  <Button
                    variant="outlined"
                    size="s"
                    onClick={() => void toggle(item.name, !item.enabled)}
                    disabled={!item.loadable && !item.enabled}
                  >
                    {item.enabled ? t('extensionsDisable') : t('extensionsEnable')}
                  </Button>
                  <Button variant="outlined" size="s" onClick={() => void move(item.name, -1)}>
                    ↑
                  </Button>
                  <Button variant="outlined" size="s" onClick={() => void move(item.name, 1)}>
                    ↓
                  </Button>
                  <Button variant="outlined" size="s" onClick={() => void openDetail(item.name)}>
                    {t('extensionsDetails')}
                  </Button>
                  {admin ? (
                    <>
                      <Button
                        variant="outlined"
                        size="s"
                        onClick={() => {
                          setUpdateTarget(item.name)
                          updateRef.current?.click()
                        }}
                      >
                        {t('extensionsUpdate')}
                      </Button>
                      <Button
                        variant="outlined"
                        size="s"
                        onClick={async () => {
                          if (!window.confirm(t('extensionsDeleteConfirm', { name: item.display_name }))) {
                            return
                          }
                          try {
                            await remove(item.name)
                          } catch (err) {
                            showToast(err instanceof Error ? err.message : String(err))
                          }
                        }}
                      >
                        {t('delete')}
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>

              {detailName === item.name && detail ? (
                <div className={styles.detail}>
                  <div>
                    {t('extensionsAuthor')}: {detail.manifest.author || '-'} · {t('extensionsOrder')}:{' '}
                    {detail.order}
                  </div>
                  {detail.compat ? (
                    <>
                      <div>
                        {t('extensionsModulesScanned', { count: detail.compat.filesScanned })}
                        {detail.compat.uiRisk.length
                          ? ` · UI 风险: ${detail.compat.uiRisk.join(', ')}`
                          : ''}
                      </div>
                      <div className={styles.modules}>
                        {detail.compat.modules.map((mod) => (
                          <div key={mod.path}>
                            {mod.status === 'ok' ? '✓' : mod.status === 'partial' ? '△' : '✗'}{' '}
                            {mod.path}
                            {mod.missing.length ? ` — 缺少 ${mod.missing.join(', ')}` : ''}
                          </div>
                        ))}
                        {detail.compat.missingModules.map((path) => (
                          <div key={path}>✗ {path} — 宿主未实现</div>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {panelInfo?.type === 'schema' ? (
                    <div>
                      <div>{t('extensionsSchemaTitle')}</div>
                      <SchemaSettingsForm
                        schema={panelInfo.schema}
                        value={settingsValue}
                        onChange={(next) => {
                          setSettingsValue(next)
                          setSettingsDraft(JSON.stringify(next, null, 2))
                        }}
                      />
                    </div>
                  ) : null}
                  {panelInfo?.type === 'iframe' ? (
                    <div className={styles.row}>
                      <Button size="s" onClick={() => setPanelOpen(true)}>
                        {t('extensionsOpenPanel')}
                      </Button>
                    </div>
                  ) : null}
                  {runtimeErrors[item.name]?.length ? (
                    <div>
                      <div>{t('extensionsRuntimeErrors')}</div>
                      <div className={styles.modules}>
                        {runtimeErrors[item.name].slice(-5).map((message, index) => (
                          <div key={index}>✗ {message}</div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <div>
                    <div>{t('extensionsSettingsTitle')}</div>
                    <textarea
                      className={styles.settingsArea}
                      value={settingsDraft}
                      onChange={(event) => setSettingsDraft(event.target.value)}
                    />
                    <div className={styles.row}>
                      <Button size="s" onClick={() => void saveSettings(item.name)}>
                        {t('save')}
                      </Button>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {panelOpen && detailName ? (
        <ExtensionPanelDialog
          name={detailName}
          displayName={detail?.manifest.display_name || detailName}
          onClose={() => setPanelOpen(false)}
          onSaved={(next) => {
            setSettingsValue(next)
            setSettingsDraft(JSON.stringify(next, null, 2))
          }}
        />
      ) : null}
    </div>
  )
}
