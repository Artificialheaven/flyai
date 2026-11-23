/**
 * 卡内授权动作的确认弹窗。
 *
 * 卡片只能发“意图”，真正执行前由宿主把完整参数摊开给用户看一次：
 * 发送全文、变量路径与体积、世界书条目数、角色字段名、网络方法与完整 URL。
 * 取消即拒绝；网络确认可勾选“允许后续所有请求”，由宿主在本会话内记住。
 */

import { useEffect, useState } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { Button } from '../base/Button.js'
import { Modal } from '../base/Modal.js'
import { cardJsonSize, type CardIntent } from '../../services/st/cardActions.js'
import styles from './CardActionDialog.module.css'

export type CardGenerateRequest = {
  kind: 'generate'
  purpose: string
  messageCount: number
  chars: number
  maxTokens: number
  preview: string
}

export type CardConfirmRequest =
  | { kind: 'intent'; intent: CardIntent }
  | { kind: 'net'; method: string; url: string; bodySize: number }
  | CardGenerateRequest

const MAX_LIST_ITEMS = 20

const formatSize = (size: number | null): string =>
  size === null ? '—' : size.toLocaleString()

const titleOf = (
  request: CardConfirmRequest,
  t: ReturnType<typeof useTranslate>
): string => {
  if (request.kind === 'net') {
    return t('cardNetTitle')
  }
  if (request.kind === 'generate') {
    return t('cardGenerateTitle')
  }
  const intent = request.intent
  if (intent.kind === 'action') {
    if (intent.action === 'send') {
      return t('cardActionSendTitle')
    }
    return intent.action === 'continue'
      ? t('cardActionContinueTitle')
      : t('cardActionRegenerateTitle')
  }
  if (intent.kind === 'vars') {
    return intent.mode === 'patch' ? t('cardVarsPatchTitle') : t('cardVarsReplaceTitle')
  }
  if (intent.kind === 'world') {
    return t('cardWorldTitle')
  }
  return t('cardCharacterTitle')
}

const SizeRow = ({ label, size }: { label: string; size: number | null }) => {
  const t = useTranslate()
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      <span className={styles.rowValue}>{t('cardActionSize', { size: formatSize(size) })}</span>
    </div>
  )
}

const IntentBody = ({ intent }: { intent: CardIntent }) => {
  const t = useTranslate()
  if (intent.kind === 'action') {
    if (intent.action === 'send') {
      return (
        <>
          <div className={styles.hint}>{t('cardActionSendHint')}</div>
          <div className={styles.sectionLabel}>{t('cardActionTextLabel')}</div>
          <pre className={styles.text}>{intent.text ?? ''}</pre>
        </>
      )
    }
    return (
      <div className={styles.hint}>
        {intent.action === 'continue'
          ? t('cardActionContinueHint')
          : t('cardActionRegenerateHint')}
      </div>
    )
  }
  if (intent.kind === 'vars') {
    if (intent.mode === 'patch') {
      const shown = intent.ops.slice(0, MAX_LIST_ITEMS)
      return (
        <>
          <div className={styles.hint}>{t('cardVarsPatchHint')}</div>
          <div className={styles.sectionLabel}>{t('cardActionOpsLabel')}</div>
          <div className={styles.list}>
            {shown.map((op, index) => (
              <div key={`${index}-${String(op.path)}`} className={styles.listRow}>
                <span className={styles.mono}>{String(op.op)}</span>
                <span className={styles.path}>{String(op.path)}</span>
              </div>
            ))}
            {intent.ops.length > shown.length ? (
              <div className={styles.more}>
                {t('cardActionMoreItems', {
                  count: intent.ops.length - shown.length,
                })}
              </div>
            ) : null}
          </div>
          <SizeRow label={t('cardActionPayloadLabel')} size={cardJsonSize(intent.ops)} />
        </>
      )
    }
    return (
      <>
        <div className={styles.hint}>{t('cardVarsReplaceHint')}</div>
        <div className={styles.row}>
          <span className={styles.rowLabel}>{t('cardActionKeysLabel')}</span>
          <span className={styles.rowValue}>
            {t('cardActionCount', { count: Object.keys(intent.stat_data).length })}
          </span>
        </div>
        <SizeRow
          label={t('cardActionPayloadLabel')}
          size={cardJsonSize(intent.stat_data)}
        />
      </>
    )
  }
  if (intent.kind === 'world') {
    return (
      <>
        <div className={styles.hint}>{t('cardWorldHint')}</div>
        <div className={styles.row}>
          <span className={styles.rowLabel}>{t('cardActionEntriesLabel')}</span>
          <span className={styles.rowValue}>
            {t('cardActionCount', { count: intent.entries.length })}
          </span>
        </div>
        <SizeRow label={t('cardActionPayloadLabel')} size={cardJsonSize(intent.entries)} />
      </>
    )
  }
  const keys = Object.keys(intent.fields)
  const shown = keys.slice(0, MAX_LIST_ITEMS)
  return (
    <>
      <div className={styles.hint}>{t('cardCharacterHint')}</div>
      <div className={styles.sectionLabel}>{t('cardActionFieldsLabel')}</div>
      <div className={styles.list}>
        {shown.map((key) => (
          <div key={key} className={styles.listRow}>
            <span className={styles.mono}>{key}</span>
            <span className={styles.rowValue}>
              {t('cardActionSize', { size: formatSize(cardJsonSize(intent.fields[key])) })}
            </span>
          </div>
        ))}
        {keys.length > shown.length ? (
          <div className={styles.more}>
            {t('cardActionMoreItems', { count: keys.length - shown.length })}
          </div>
        ) : null}
      </div>
    </>
  )
}

const NetBody = ({
  request,
}: {
  request: Extract<CardConfirmRequest, { kind: 'net' }>
}) => {
  const t = useTranslate()
  return (
    <>
      <div className={styles.hint}>{t('cardNetHint')}</div>
      <div className={styles.sectionLabel}>{t('cardActionNetTargetLabel')}</div>
      <div className={styles.netTarget}>
        <span className={styles.mono}>{request.method}</span>
        <span className={styles.url}>{request.url}</span>
      </div>
      <SizeRow label={t('cardActionPayloadLabel')} size={request.bodySize} />
    </>
  )
}

const GenerateBody = ({ request }: { request: CardGenerateRequest }) => {
  const t = useTranslate()
  return (
    <>
      <div className={styles.hint}>{t('cardGenerateHint')}</div>
      <div className={styles.row}>
        <span className={styles.rowLabel}>{t('cardGeneratePurposeLabel')}</span>
        <span className={styles.rowValue}>{request.purpose || 'phone'}</span>
      </div>
      <div className={styles.row}>
        <span className={styles.rowLabel}>{t('cardGenerateMessagesLabel')}</span>
        <span className={styles.rowValue}>
          {t('cardActionCount', { count: request.messageCount })}
        </span>
      </div>
      <SizeRow label={t('cardActionPayloadLabel')} size={request.chars} />
      {request.maxTokens > 0 ? (
        <div className={styles.row}>
          <span className={styles.rowLabel}>{t('cardGenerateMaxTokensLabel')}</span>
          <span className={styles.rowValue}>{request.maxTokens}</span>
        </div>
      ) : null}
      {request.preview ? (
        <>
          <div className={styles.sectionLabel}>{t('cardGeneratePreviewLabel')}</div>
          <pre className={styles.text}>{request.preview}</pre>
        </>
      ) : null}
    </>
  )
}

export type CardConfirmOptions = { allowAll?: boolean }

export const CardActionDialog = ({
  request,
  onConfirm,
  onCancel,
}: {
  request: CardConfirmRequest
  onConfirm: (options?: CardConfirmOptions) => void
  onCancel: () => void
}) => {
  const t = useTranslate()
  const [allowAll, setAllowAll] = useState(false)
  // 连续确认不同请求时组件可能复用，进入新请求重置勾选
  useEffect(() => {
    setAllowAll(false)
  }, [request])
  return (
    <Modal
      title={titleOf(request, t)}
      className={styles.dialog}
      closable
      onClose={onCancel}
      footer={
        <>
          <Button variant="outlined" onClick={onCancel}>
            {t('cancel')}
          </Button>
          <Button
            onClick={() =>
              onConfirm(request.kind === 'net' ? { allowAll } : undefined)
            }
          >
            {t('cardActionConfirm')}
          </Button>
        </>
      }
    >
      <div className={styles.body}>
        {request.kind === 'net' ? (
          <>
            <NetBody request={request} />
            <label className={styles.allowAll}>
              <input
                type="checkbox"
                checked={allowAll}
                onChange={(event) => setAllowAll(event.target.checked)}
              />
              <span>{t('cardNetAllowAll')}</span>
            </label>
          </>
        ) : request.kind === 'generate' ? (
          <GenerateBody request={request} />
        ) : (
          <IntentBody intent={request.intent} />
        )}
      </div>
    </Modal>
  )
}
