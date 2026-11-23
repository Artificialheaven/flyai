import { useEffect } from 'react'
import { useTranslate } from '../../i18n/index.js'
import type { PromptArchive } from '../../services/flyai/sessions.js'

/** 查看某条回复生成时实际发给模型的提示词（只读弹层）。 */
export const PromptDialog = ({
  data,
  onClose,
}: {
  data: PromptArchive | null
  onClose: () => void
}) => {
  const t = useTranslate()

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!data) return null
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 80,
        background: 'rgba(0,0,0,.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        style={{
          background: 'var(--color-bg, #fff)',
          color: 'inherit',
          borderRadius: 12,
          width: 'min(920px, 100%)',
          maxHeight: '82vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 12px 48px rgba(0,0,0,.25)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px',
            borderBottom: '1px solid rgba(127,127,127,.2)',
            fontWeight: 600,
          }}
        >
          <span>{t('promptDialogTitle')}</span>
          <button
            type="button"
            onClick={onClose}
            style={{ border: 0, background: 'transparent', cursor: 'pointer', fontSize: 18 }}
            aria-label="close"
          >
            ×
          </button>
        </div>
        <div style={{ overflow: 'auto', padding: '12px 16px', fontSize: 12, lineHeight: 1.6 }}>
          {data.calls.length === 0 ? (
            <div>{t('promptDialogEmpty')}</div>
          ) : (
            data.calls.map((call, index) => (
              <details key={index} open={index === data.calls.length - 1} style={{ marginBottom: 14 }}>
                <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                  #{index + 1} {call.kind} · {call.messages.length} messages · {call.tools?.length || 0} tools
                </summary>
                {call.params && Object.keys(call.params).length > 0 ? (
                  <div style={{ color: '#888', margin: '6px 0' }}>{JSON.stringify(call.params)}</div>
                ) : null}
                {call.messages.map((message, messageIndex) => (
                  <div
                    key={messageIndex}
                    style={{
                      margin: '8px 0',
                      paddingLeft: 8,
                      borderLeft: '3px solid rgba(127,127,127,.35)',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    <b>
                      [{message.role}
                      {message.name ? ` ${message.name}` : ''}]
                    </b>
                    {'\n'}
                    {message.content || ''}
                  </div>
                ))}
                {call.tools && call.tools.length > 0 ? (
                  <details>
                    <summary style={{ cursor: 'pointer' }}>tools ({call.tools.length})</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify(call.tools, null, 1)}
                    </pre>
                  </details>
                ) : null}
              </details>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
