import { useEffect, useRef, useState } from 'react'
import { Modal } from '../base/Modal.js'
import { Button } from '../base/Button.js'
import { showToast } from '../base/toastState.js'
import { useTranslate } from '../../i18n/index.js'
import * as client from '../../services/extensions/client.js'
import type { ExtensionHostState } from '../../services/extensions/types.js'
import { buildPanelDoc } from '../../services/extensions/panelDoc.js'
import styles from './ExtensionPanel.module.css'

const EXT_NS = 'flyai-ext'

/** L2 独立面板：panel.html 在沙箱 iframe 内运行，只开放 settings/toast/popup。 */
export const ExtensionPanelDialog = ({
  name,
  displayName,
  onClose,
  onSaved,
}: {
  name: string
  displayName: string
  onClose: () => void
  onSaved: (settings: Record<string, unknown>) => void
}) => {
  const t = useTranslate()
  const frame = useRef<HTMLIFrameElement | null>(null)
  const [doc, setDoc] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const settingsRef = useRef<Record<string, unknown>>({})

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [detail, panel] = await Promise.all([
          client.getExtension(name),
          client.getExtensionPanel(name),
        ])
        if (panel.type !== 'iframe') {
          throw new Error('该扩展没有 panel.html')
        }
        const url = `${panel.url}`
        const response = await fetch(url)
        if (!response.ok) throw new Error(`面板加载失败（HTTP ${response.status}）`)
        const html = await response.text()
        const capabilities = (detail.manifest.flyai?.capabilities || []) as ExtensionHostState['capabilities']
        const state: ExtensionHostState = {
          name,
          baseUrl: `/scripts/extensions/third-party/${encodeURIComponent(name)}/`,
          js: detail.manifest.js,
          css: detail.manifest.css,
          manifest: detail.manifest,
          settings: detail.settings || {},
          capabilities,
          order: detail.order,
        }
        settingsRef.current = { ...(detail.settings || {}) }
        const built = buildPanelDoc(
          html,
          {
            state,
            settings: settingsRef.current,
            snapshot: {
              chat_id: '',
              characters: [],
              chat: [],
              chat_metadata: {},
              name2: '',
              user_name: 'User',
              user_avatar: '',
              character_avatar: '',
              is_mobile: false,
              max_prompt_tokens: 8192,
              variables: {},
              extension_names: [],
              context: {},
            },
          },
          location.origin
        )
        if (!cancelled) {
          setDoc(built)
          setLoading(false)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
          setLoading(false)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [name])

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return
      const data = event.data as Record<string, unknown> | null
      if (!data || data.ns !== EXT_NS || data.v !== 1) return
      const id = String(data.id || '')
      const reply = (result?: unknown, error?: { message: string }) => {
        frame.current?.contentWindow?.postMessage(
          { v: 1, ns: EXT_NS, type: 'rpc-result', id, ...(error ? { error } : { result }) },
          '*'
        )
      }
      const method = String(data.method || '')
      const params = (data.params || {}) as Record<string, unknown>
      if (data.type === 'ready') {
        frame.current?.contentWindow?.postMessage(
          { v: 1, ns: EXT_NS, type: 'settings', settings: settingsRef.current },
          '*'
        )
        return
      }
      if (data.type !== 'rpc') return
      if (method === 'settings.get') {
        reply({ settings: settingsRef.current })
      } else if (method === 'settings.save') {
        const next = (params.settings || {}) as Record<string, unknown>
        settingsRef.current = next
        void client
          .putExtensionSettings(name, next)
          .then(() => {
            onSaved(next)
            showToast({ key: 'saved' })
            reply({ ok: true })
          })
          .catch((err) => reply(undefined, { message: err instanceof Error ? err.message : String(err) }))
      } else if (method === 'ui.toast') {
        showToast(String(params.message || ''))
        reply({ ok: true })
      } else if (method === 'ui.popup') {
        reply({ result: window.confirm(String(params.message || '')) ? 1 : 0 })
      } else if (method === 'log') {
        reply({ ok: true })
      } else {
        reply(undefined, { message: `panel RPC not allowed: ${method}` })
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [name, onSaved])

  return (
    <Modal
      title={`${displayName} · ${t('extensionsPanelTitle')}`}
      closable
      onClose={onClose}
      footer={
        <Button variant="outlined" onClick={onClose}>
          {t('close')}
        </Button>
      }
    >
      {loading ? <div className={styles.empty}>{t('extensionsLoading')}</div> : null}
      {error ? <div className={styles.error}>{error}</div> : null}
      {doc ? (
        <iframe
          ref={frame}
          title={displayName}
          sandbox="allow-scripts"
          srcDoc={doc}
          className={styles.panelFrame}
        />
      ) : null}
    </Modal>
  )
}
