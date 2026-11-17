import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import type { TranslationKey } from '../../../i18n/index.js'
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  listPrompts,
  listWatchers,
  reconcileWatchers,
  resetPrompt,
  setMcpEnabled,
  setPrompt,
  stopWatcher,
  testMcpServer,
  updateMcpServer,
  type McpServer,
  type PromptPartition,
  type Watcher,
} from '../../../services/flyai/agentdb.js'
import { useAuth } from '../../../services/flyai/auth.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { showToast } from '../../base/toastState.js'
import { PanelEmpty, PanelError, Toggle, errorText } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

type Tab = 'mcp' | 'watchers' | 'prompts'

const tabs: { value: Tab; label: TranslationKey }[] = [
  { value: 'mcp', label: 'integrationTabMcp' },
  { value: 'watchers', label: 'integrationTabWatchers' },
  { value: 'prompts', label: 'integrationTabPrompts' },
]

const SERVER_ID_PATTERN = /^[a-z][a-z0-9_]{0,31}$/

type McpForm = {
  serverId: string
  command: string
  args: string
  env: string
  enabled: boolean
}

const emptyForm: McpForm = { serverId: '', command: '', args: '', env: '', enabled: true }

const toForm = (server: McpServer): McpForm => ({
  serverId: server.server_id,
  command: server.command,
  args: server.args.join('\n'),
  env: Object.entries(server.env)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n'),
  enabled: Boolean(server.enabled),
})

const parseLines = (text: string): string[] =>
  text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

/** 解析 KEY=VALUE 行；非法行返回 null 由调用方提示 */
const parseEnv = (text: string): Record<string, string> | null => {
  const env: Record<string, string> = {}
  for (const line of parseLines(text)) {
    const index = line.indexOf('=')
    if (index <= 0) {
      return null
    }
    env[line.slice(0, index).trim()] = line.slice(index + 1).trim()
  }
  return env
}

const formatTime = (value: number | undefined): string =>
  value ? new Date(value * 1000).toLocaleString() : ''

/** 生态接入（仅管理员）：MCP server、监测进程与提示词分区。 */
export const IntegrationPanel = () => {
  const t = useTranslate()
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [tab, setTab] = useState<Tab>('mcp')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [servers, setServers] = useState<McpServer[]>([])
  const [editing, setEditing] = useState<McpServer | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<McpForm>(emptyForm)
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<McpServer | null>(null)
  const [busyServer, setBusyServer] = useState('')

  const [watchers, setWatchers] = useState<Watcher[]>([])
  const [busyWatcher, setBusyWatcher] = useState(0)

  const [prompts, setPrompts] = useState<PromptPartition[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busyPrompt, setBusyPrompt] = useState('')

  const loadMcp = useCallback(async () => {
    try {
      const data = await listMcpServers()
      setServers(data.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [t])

  const loadWatchers = useCallback(async () => {
    try {
      const data = await listWatchers()
      setWatchers(data.items)
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [t])

  const loadPrompts = useCallback(async () => {
    try {
      const data = await listPrompts()
      setPrompts(data.items)
      setDrafts(Object.fromEntries(data.items.map((item) => [item.key, item.override])))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    }
  }, [t])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    await Promise.all([loadMcp(), loadWatchers(), loadPrompts()])
    setLoading(false)
  }, [loadMcp, loadWatchers, loadPrompts])

  useEffect(() => {
    if (isAdmin) {
      void load()
    }
  }, [isAdmin, load])

  const openCreate = () => {
    setForm(emptyForm)
    setFormError('')
    setCreating(true)
  }

  const openEdit = (server: McpServer) => {
    setForm(toForm(server))
    setFormError('')
    setEditing(server)
  }

  const closeForm = () => {
    if (!saving) {
      setCreating(false)
      setEditing(null)
    }
  }

  const submitForm = async () => {
    if (saving) {
      return
    }
    const serverId = form.serverId.trim()
    const command = form.command.trim()
    const env = parseEnv(form.env)
    if (!SERVER_ID_PATTERN.test(serverId) || !command || env === null) {
      setFormError(t('integrationMcpInvalid'))
      return
    }
    setSaving(true)
    setFormError('')
    try {
      if (editing) {
        await updateMcpServer(editing.server_id, {
          command,
          args: parseLines(form.args),
          env,
          enabled: form.enabled,
        })
        showToast({ key: 'integrationMcpUpdated' })
      } else {
        await createMcpServer({
          serverId,
          command,
          args: parseLines(form.args),
          env,
          enabled: form.enabled,
        })
        showToast({ key: 'integrationMcpCreated' })
      }
      setCreating(false)
      setEditing(null)
      await loadMcp()
    } catch (err) {
      setFormError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setSaving(false)
    }
  }

  const toggleServer = async (server: McpServer, enabled: boolean) => {
    setBusyServer(server.server_id)
    try {
      const result = await setMcpEnabled(server.server_id, enabled)
      setServers((current) =>
        current.map((item) => (item.server_id === server.server_id ? result.item : item))
      )
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyServer('')
    }
  }

  const testServer = async (server: McpServer) => {
    setBusyServer(server.server_id)
    try {
      const result = await testMcpServer(server.server_id)
      showToast({
        key: 'integrationMcpTestOk',
        params: { count: result.tools.length },
      })
      await loadMcp()
    } catch (err) {
      showToast({
        key: 'integrationMcpTestFailed',
        params: { message: errorText(err) },
        type: 'error',
      })
    } finally {
      setBusyServer('')
    }
  }

  const confirmDelete = async () => {
    if (!deleting) {
      return
    }
    try {
      await deleteMcpServer(deleting.server_id)
      setDeleting(null)
      showToast({ key: 'adminDeleted' })
      await loadMcp()
    } catch (err) {
      setError(t('adminDeleteFailed', { message: errorText(err) }))
    }
  }

  const stop = async (watcher: Watcher) => {
    setBusyWatcher(watcher.id)
    try {
      await stopWatcher(watcher.id)
      showToast({ key: 'integrationWatcherStopped' })
      await loadWatchers()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyWatcher(0)
    }
  }

  const reconcile = async () => {
    try {
      const result = await reconcileWatchers()
      showToast({ key: 'integrationWatcherReconciled', params: { count: result.stopped } })
      await loadWatchers()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    }
  }

  const savePrompt = async (partition: PromptPartition) => {
    const text = drafts[partition.key] ?? ''
    setBusyPrompt(partition.key)
    try {
      if (!text.trim()) {
        await resetPrompt(partition.key)
        showToast({ key: 'integrationPromptReset' })
      } else {
        await setPrompt(partition.key, text)
        showToast({ key: 'integrationPromptSaved' })
      }
      await loadPrompts()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyPrompt('')
    }
  }

  const resetDraft = async (partition: PromptPartition) => {
    setBusyPrompt(partition.key)
    try {
      await resetPrompt(partition.key)
      showToast({ key: 'integrationPromptReset' })
      await loadPrompts()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusyPrompt('')
    }
  }

  if (!isAdmin) {
    return (
      <div className={styles.page}>
        <PanelEmpty text={t('integrationNotAdmin')} />
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideIntegrationOptionLabel')}</div>
          <div className={styles.subtitle}>{t('integrationSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          {tab === 'mcp' ? (
            <Button size="s" onClick={openCreate}>
              {t('integrationMcpAdd')}
            </Button>
          ) : null}
          {tab === 'watchers' ? (
            <Button variant="outlined" size="s" onClick={() => void reconcile()}>
              {t('integrationWatcherReconcile')}
            </Button>
          ) : null}
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}

      <div className={styles.tabs} role="tablist">
        {tabs.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={tab === item.value}
            className={`${styles.tab} ${tab === item.value ? styles.tabActive : ''}`}
            onClick={() => setTab(item.value)}
          >
            {t(item.label)}
          </button>
        ))}
      </div>

      {loading ? <PanelEmpty text={t('adminLoading')} /> : null}

      {tab === 'mcp' && !loading ? (
        servers.length === 0 ? (
          <PanelEmpty text={t('integrationMcpEmpty')} />
        ) : (
          <div className={styles.list}>
            {servers.map((server) => (
              <div key={server.server_id} className={styles.item}>
                <div className={styles.itemMain}>
                  <div className={styles.itemName}>
                    {server.server_id}
                    <span className={styles.badge}>{server.status || 'unknown'}</span>
                    {server.enabled ? null : <span className={styles.badge}>{t('adminOff')}</span>}
                  </div>
                  <div className={styles.itemMeta}>
                    {[server.command, ...server.args].join(' ')}
                  </div>
                  {server.last_error ? (
                    <div className={styles.itemMeta}>{server.last_error}</div>
                  ) : null}
                  <div className={styles.chips}>
                    {server.tools.length ? (
                      <span className={styles.chip}>
                        {t('integrationMcpTools', { count: server.tools.length })}
                      </span>
                    ) : null}
                    {server.tools.slice(0, 6).map((tool, index) => (
                      <span key={`${tool.name ?? index}`} className={styles.chip}>
                        {tool.name ?? 'tool'}
                      </span>
                    ))}
                  </div>
                </div>
                <div className={styles.itemActions}>
                  <Toggle
                    checked={Boolean(server.enabled)}
                    label={server.server_id}
                    disabled={busyServer === server.server_id}
                    onChange={(checked) => void toggleServer(server, checked)}
                  />
                  <Button
                    variant="outlined"
                    size="s"
                    disabled={busyServer === server.server_id}
                    onClick={() => void testServer(server)}
                  >
                    {t('adminTest')}
                  </Button>
                  <Button variant="outlined" size="s" onClick={() => openEdit(server)}>
                    {t('adminEdit')}
                  </Button>
                  <Button variant="dangerOutlined" size="s" onClick={() => setDeleting(server)}>
                    {t('adminDelete')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )
      ) : null}

      {tab === 'watchers' && !loading ? (
        watchers.length === 0 ? (
          <PanelEmpty text={t('integrationWatcherEmpty')} />
        ) : (
          <div className={styles.list}>
            {watchers.map((watcher) => (
              <div key={watcher.id} className={styles.item}>
                <div className={styles.itemMain}>
                  <div className={styles.itemName}>
                    {watcher.script_path.split(/[\\/]/).pop() || watcher.script_path}
                    <span className={styles.badge}>{watcher.status}</span>
                    {watcher.pid ? <span className={styles.badge}>PID {watcher.pid}</span> : null}
                  </div>
                  <div className={styles.itemMeta}>
                    {t('integrationWatcherSession', { id: watcher.session_id })} ·{' '}
                    {formatTime(watcher.created_at)}
                  </div>
                </div>
                <div className={styles.itemActions}>
                  <Button
                    variant="dangerOutlined"
                    size="s"
                    disabled={busyWatcher === watcher.id}
                    onClick={() => void stop(watcher)}
                  >
                    {t('integrationWatcherStop')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )
      ) : null}

      {tab === 'prompts' && !loading ? (
        prompts.length === 0 ? (
          <PanelEmpty text={t('integrationPromptEmpty')} />
        ) : (
          <div className={styles.list}>
            {prompts.map((partition) => (
              <div key={partition.key} className={styles.card}>
                <div className={styles.cardHead}>
                  <div>
                    <div className={styles.itemName}>
                      {partition.key}
                      {partition.override ? (
                        <span className={`${styles.badge} ${styles.badgeBrand}`}>
                          {t('integrationPromptOverridden')}
                        </span>
                      ) : null}
                    </div>
                    <div className={styles.itemMeta}>{partition.desc}</div>
                  </div>
                  <div className={styles.actions}>
                    <Button
                      variant="outlined"
                      size="s"
                      disabled={busyPrompt === partition.key}
                      onClick={() => void resetDraft(partition)}
                    >
                      {t('integrationPromptResetAction')}
                    </Button>
                    <Button
                      size="s"
                      disabled={busyPrompt === partition.key}
                      onClick={() => void savePrompt(partition)}
                    >
                      {t('adminSave')}
                    </Button>
                  </div>
                </div>
                {partition.placeholders.length ? (
                  <div className={styles.chips}>
                    {partition.placeholders.map((placeholder) => (
                      <span key={placeholder} className={styles.chip}>
                        {placeholder}
                      </span>
                    ))}
                  </div>
                ) : null}
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>{t('integrationPromptOverride')}</span>
                  <textarea
                    className={styles.textarea}
                    rows={6}
                    value={drafts[partition.key] ?? ''}
                    placeholder={t('integrationPromptPlaceholder')}
                    onChange={(event) =>
                      setDrafts((current) => ({ ...current, [partition.key]: event.target.value }))
                    }
                  />
                </label>
              </div>
            ))}
          </div>
        )
      ) : null}

      {creating || editing ? (
        <Modal
          title={editing ? t('integrationMcpEditTitle') : t('integrationMcpAdd')}
          className={styles.dialog}
          onClose={closeForm}
          footer={
            <div className={styles.dialogFooter}>
              <Button variant="outlined" disabled={saving} onClick={closeForm}>
                {t('cancel')}
              </Button>
              <Button disabled={saving} onClick={() => void submitForm()}>
                {t(saving ? 'adminSaving' : 'adminSave')}
              </Button>
            </div>
          }
        >
          <div className={styles.dialogMain}>
            {formError ? <PanelError message={formError} /> : null}
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('integrationMcpFieldId')}</span>
              <input
                className={styles.input}
                value={form.serverId}
                disabled={Boolean(editing)}
                placeholder="filesystem"
                onChange={(event) => setForm((current) => ({ ...current, serverId: event.target.value }))}
              />
              <span className={styles.hint}>{t('integrationMcpFieldIdHint')}</span>
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('integrationMcpFieldCommand')}</span>
              <input
                className={styles.input}
                value={form.command}
                placeholder="npx"
                onChange={(event) => setForm((current) => ({ ...current, command: event.target.value }))}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('integrationMcpFieldArgs')}</span>
              <textarea
                className={styles.textarea}
                rows={3}
                value={form.args}
                onChange={(event) => setForm((current) => ({ ...current, args: event.target.value }))}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('integrationMcpFieldEnv')}</span>
              <textarea
                className={styles.textarea}
                rows={3}
                value={form.env}
                placeholder="KEY=VALUE"
                onChange={(event) => setForm((current) => ({ ...current, env: event.target.value }))}
              />
            </label>
            <label className={styles.checkRow}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) =>
                  setForm((current) => ({ ...current, enabled: event.target.checked }))
                }
              />
              {t('integrationMcpFieldEnabled')}
            </label>
          </div>
        </Modal>
      ) : null}

      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => setDeleting(null)}
          footer={
            <div className={styles.dialogFooter}>
              <Button variant="outlined" onClick={() => setDeleting(null)}>
                {t('cancel')}
              </Button>
              <Button variant="danger" onClick={() => void confirmDelete()}>
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('integrationMcpDeleteDesc', { name: deleting.server_id })}
        </Modal>
      ) : null}
    </div>
  )
}