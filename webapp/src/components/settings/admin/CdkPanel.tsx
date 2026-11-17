import { useCallback, useEffect, useState } from 'react'
import { useTranslate } from '../../../i18n/index.js'
import {
  deleteCdk,
  disableCdk,
  exportCdks,
  extractCdk,
  generateCdks,
  importCdks,
  listCdks,
  type CdkImportResult,
  type CdkStatus,
  type CdkView,
} from '../../../services/flyai/cdk.js'
import { copyText } from '../../../utils/clipboard.js'
import { Button } from '../../base/Button.js'
import { Modal } from '../../base/Modal.js'
import { showToast } from '../../base/toastState.js'
import { errorText, NumberField, PanelEmpty, PanelError, TextField } from './AdminCommon.js'
import styles from './AdminPanels.module.css'

const formatNumber = (value: number): string =>
  (Math.round(value * 10) / 10).toLocaleString()

const statusKey = (status: CdkStatus) => {
  if (status === 'expired') {
    return 'cdkStatusExpired' as const
  }
  if (status === 'exhausted') {
    return 'cdkStatusExhausted' as const
  }
  if (status === 'disabled') {
    return 'cdkStatusDisabled' as const
  }
  return 'cdkStatusUsable' as const
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

const copy = async (text: string) => {
  const ok = await copyText(text)
  showToast({ key: ok ? 'copied' : 'copyFailed' })
}

/** CDK 管理（仅管理员）：生成、粘贴导入、列表、取出、停用、删除、导出 */
export const CdkPanel = () => {
  const t = useTranslate()
  const [cdks, setCdks] = useState<CdkView[]>([])
  const [status, setStatus] = useState<CdkStatus>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyCode, setBusyCode] = useState('')
  const [generating, setGenerating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [extracted, setExtracted] = useState<CdkView | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [deleting, setDeleting] = useState<CdkView | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setCdks(await listCdks(status))
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setLoading(false)
    }
  }, [t, status])

  useEffect(() => {
    void load()
  }, [load])

  const confirmDelete = async () => {
    if (!deleting || busyCode) {
      return
    }
    setBusyCode(deleting.code)
    setError('')
    try {
      await deleteCdk(deleting.code)
      setDeleting(null)
      showToast({ key: 'adminDeleted' })
      await load()
    } catch (err) {
      setError(t('adminDeleteFailed', { message: errorText(err) }))
    } finally {
      setBusyCode('')
    }
  }

  const disableOne = async (cdk: CdkView) => {
    if (busyCode) {
      return
    }
    setBusyCode(cdk.code)
    setError('')
    try {
      await disableCdk(cdk.code)
      showToast({ key: 'cdkDisabledToast' })
      await load()
    } catch (err) {
      setError(t('cdkDisableFailed', { message: errorText(err) }))
    } finally {
      setBusyCode('')
    }
  }

  const extractOne = async () => {
    if (extracting) {
      return
    }
    setExtracting(true)
    setError('')
    try {
      const cdk = await extractCdk()
      if (!cdk) {
        showToast({ key: 'cdkExtractEmpty' })
      } else {
        setExtracted(cdk)
      }
    } catch (err) {
      setError(t('adminLoadFailed', { message: errorText(err) }))
    } finally {
      setExtracting(false)
    }
  }

  const exportAll = async () => {
    if (exporting) {
      return
    }
    setExporting(true)
    setError('')
    try {
      const text = await exportCdks()
      const ok = await copyText(text)
      showToast({ key: ok ? 'cdkExportDone' : 'copyFailed' })
    } catch (err) {
      setError(t('cdkExportFailed', { message: errorText(err) }))
    } finally {
      setExporting(false)
    }
  }

  const tabs: { value: CdkStatus; label: string }[] = [
    { value: '', label: t('cdkTabsAll') },
    { value: 'usable', label: t('cdkStatusUsable') },
    { value: 'expired', label: t('cdkStatusExpired') },
    { value: 'exhausted', label: t('cdkStatusExhausted') },
    { value: 'disabled', label: t('cdkStatusDisabled') },
  ]

  const meta = (cdk: CdkView): string =>
    t('cdkMeta', {
      amount: formatNumber(cdk.amount),
      used: formatNumber(cdk.used),
      max: cdk.max_uses > 0 ? formatNumber(cdk.max_uses) : t('cdkUnlimited'),
      expires: cdk.expires_at || t('cdkPermanent'),
    })

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t('settingDialogAsideCdkOptionLabel')}</div>
          <div className={styles.subtitle}>{t('cdkSubtitle')}</div>
        </div>
        <div className={styles.actions}>
          <Button variant="outlined" size="s" disabled={loading} onClick={() => void load()}>
            {t('adminRefresh')}
          </Button>
          <Button variant="outlined" size="s" disabled={extracting} onClick={() => void extractOne()}>
            {t('cdkExtract')}
          </Button>
          <Button variant="outlined" size="s" disabled={exporting} onClick={() => void exportAll()}>
            {t('cdkExport')}
          </Button>
          <Button size="s" onClick={() => setImporting(true)}>
            {t('cdkImport')}
          </Button>
          <Button size="s" onClick={() => setGenerating(true)}>
            {t('cdkGenerate')}
          </Button>
        </div>
      </div>
      {error ? <PanelError message={error} /> : null}

      <div className={styles.tabs}>
        {tabs.map((tab) => (
          <button
            key={tab.value || 'all'}
            type="button"
            className={tab.value === status ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            onClick={() => setStatus(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {loading ? (
        <PanelEmpty text={t('adminLoading')} />
      ) : cdks.length === 0 ? (
        <PanelEmpty text={t('cdkEmpty')} />
      ) : (
        <div className={styles.list}>
          {cdks.map((cdk) => (
            <div key={cdk.code} className={styles.item}>
              <div className={styles.itemMain}>
                <div className={styles.itemName}>
                  <span>{cdk.code}</span>
                  <span className={cdk.status === 'usable' ? `${styles.badge} ${styles.badgeBrand}` : styles.badge}>
                    {t(statusKey(cdk.status))}
                  </span>
                </div>
                <div className={styles.itemMeta}>{meta(cdk)}</div>
              </div>
              <div className={styles.itemActions}>
                <Button
                  size="s"
                  variant="outlined"
                  onClick={() => void copy(cdk.code)}
                >
                  {t('copy')}
                </Button>
                <Button
                  size="s"
                  variant="outlined"
                  disabled={busyCode === cdk.code || cdk.disabled}
                  onClick={() => void disableOne(cdk)}
                >
                  {t('cdkDisable')}
                </Button>
                <Button
                  size="s"
                  variant="dangerOutlined"
                  disabled={busyCode === cdk.code}
                  onClick={() => setDeleting(cdk)}
                >
                  {t('adminDelete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {generating ? (
        <GenerateForm
          onClose={() => setGenerating(false)}
          onSaved={() => {
            void load()
          }}
        />
      ) : null}
      {importing ? (
        <ImportForm
          onClose={() => setImporting(false)}
          onSaved={() => {
            void load()
          }}
        />
      ) : null}
      {extracted ? (
        <Modal
          title={t('cdkExtractTitle')}
          closable
          className={styles.dialog}
          onClose={() => setExtracted(null)}
          footer={
            <div className={styles.dialogFooter}>
              <Button variant="outlined" size="m" onClick={() => setExtracted(null)}>
                {t('close')}
              </Button>
              <Button size="m" onClick={() => void copy(extracted.code)}>
                {t('copy')}
              </Button>
            </div>
          }
        >
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('cdkCodeLabel')}</span>
            <input className={styles.input} readOnly value={extracted.code} />
          </div>
          <div className={styles.hint}>{meta(extracted)}</div>
        </Modal>
      ) : null}
      {deleting ? (
        <Modal
          title={t('adminConfirmDeleteTitle')}
          className={styles.dialog}
          onClose={() => (busyCode ? undefined : setDeleting(null))}
          footer={
            <div className={styles.dialogFooter}>
              <Button
                variant="outlined"
                size="m"
                disabled={Boolean(busyCode)}
                onClick={() => setDeleting(null)}
              >
                {t('cancel')}
              </Button>
              <Button
                variant="danger"
                size="m"
                disabled={Boolean(busyCode)}
                onClick={() => void confirmDelete()}
              >
                {t('adminDelete')}
              </Button>
            </div>
          }
        >
          {t('cdkDeleteDesc', { code: deleting.code })}
        </Modal>
      ) : null}
    </div>
  )
}

/** 批量生成：数量 1–500；生成成功后直接列出新码供复制 */
const GenerateForm = ({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const [count, setCount] = useState('1')
  const [amount, setAmount] = useState('0')
  const [maxUses, setMaxUses] = useState('1')
  const [expires, setExpires] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<CdkView[] | null>(null)

  const submit = async () => {
    if (busy) {
      return
    }
    const countValue = Number(count)
    if (!Number.isInteger(countValue) || countValue < 1 || countValue > 500) {
      setError(t('cdkCountInvalid'))
      return
    }
    const amountValue = Number(amount)
    if (!Number.isFinite(amountValue) || amountValue < 0) {
      setError(t('cdkAmountInvalid'))
      return
    }
    const maxValue = Number(maxUses)
    if (!Number.isInteger(maxValue) || maxValue < 0) {
      setError(t('cdkMaxUsesInvalid'))
      return
    }
    if (expires.trim() && !DATE_RE.test(expires.trim())) {
      setError(t('cdkExpiresInvalid'))
      return
    }
    setBusy(true)
    setError('')
    try {
      const cds = await generateCdks({
        count: countValue,
        amount: amountValue,
        max_uses: maxValue,
        expires_at: expires.trim(),
      })
      setCreated(cds)
      showToast({ key: 'cdkGenerateDone', params: { count: cds.length } })
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t(created ? 'cdkCreatedTitle' : 'cdkGenerateTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        created ? (
          <div className={styles.dialogFooter}>
            <Button variant="outlined" size="m" onClick={() => void copy(created.map((item) => item.code).join('\n'))}>
              {t('cdkCopyAll')}
            </Button>
            <Button size="m" onClick={onClose}>
              {t('close')}
            </Button>
          </div>
        ) : (
          <div className={styles.dialogFooter}>
            <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
              {t('cancel')}
            </Button>
            <Button size="m" disabled={busy} onClick={() => void submit()}>
              {t(busy ? 'adminSaving' : 'cdkGenerate')}
            </Button>
          </div>
        )
      }
    >
      {error ? <PanelError message={error} /> : null}
      {created ? (
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('cdkCreatedList', { count: created.length })}</span>
          <textarea
            className={styles.textarea}
            readOnly
            rows={Math.min(12, Math.max(4, created.length))}
            value={created.map((item) => item.code).join('\n')}
          />
        </label>
      ) : (
        <>
          <NumberField
            label={t('cdkFieldCount')}
            hint={t('cdkFieldCountHint')}
            value={count}
            onChange={setCount}
          />
          <div className={styles.inlineFields}>
            <NumberField
              label={t('cdkFieldAmount')}
              hint={t('cdkFieldAmountHint')}
              value={amount}
              onChange={setAmount}
            />
            <NumberField
              label={t('cdkFieldMaxUses')}
              hint={t('cdkFieldMaxUsesHint')}
              value={maxUses}
              onChange={setMaxUses}
            />
          </div>
          <TextField
            label={t('cdkFieldExpires')}
            hint={t('cdkFieldExpiresHint')}
            value={expires}
            placeholder="2099-12-31"
            onChange={setExpires}
          />
        </>
      )}
    </Modal>
  )
}

/** 粘贴导入：一行一个（兼容逗号/空格分隔），返回新增/重复/无效统计 */
const ImportForm = ({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) => {
  const t = useTranslate()
  const [codes, setCodes] = useState('')
  const [amount, setAmount] = useState('0')
  const [maxUses, setMaxUses] = useState('1')
  const [expires, setExpires] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<CdkImportResult | null>(null)

  const submit = async () => {
    if (busy) {
      return
    }
    if (!codes.trim()) {
      setError(t('cdkImportEmpty'))
      return
    }
    const amountValue = Number(amount)
    if (!Number.isFinite(amountValue) || amountValue < 0) {
      setError(t('cdkAmountInvalid'))
      return
    }
    const maxValue = Number(maxUses)
    if (!Number.isInteger(maxValue) || maxValue < 0) {
      setError(t('cdkMaxUsesInvalid'))
      return
    }
    if (expires.trim() && !DATE_RE.test(expires.trim())) {
      setError(t('cdkExpiresInvalid'))
      return
    }
    setBusy(true)
    setError('')
    try {
      const next = await importCdks({
        codes,
        amount: amountValue,
        max_uses: maxValue,
        expires_at: expires.trim(),
      })
      setResult(next)
      showToast({
        key: 'cdkImportDone',
        params: {
          added: next.added.length,
          duplicates: next.duplicates.length,
          invalid: next.invalid.length,
        },
      })
      onSaved()
    } catch (err) {
      setError(t('adminSaveFailed', { message: errorText(err) }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t(result ? 'cdkImportResultTitle' : 'cdkImportTitle')}
      closable
      className={styles.dialog}
      mainClassName={styles.dialogMain}
      onClose={() => (busy ? undefined : onClose())}
      footer={
        result ? (
          <div className={styles.dialogFooter}>
            {result.added.length > 0 ? (
              <Button
                variant="outlined"
                size="m"
                onClick={() => void copy(result.added.join('\n'))}
              >
                {t('cdkCopyAdded')}
              </Button>
            ) : null}
            <Button size="m" onClick={onClose}>
              {t('close')}
            </Button>
          </div>
        ) : (
          <div className={styles.dialogFooter}>
            <Button variant="outlined" size="m" disabled={busy} onClick={onClose}>
              {t('cancel')}
            </Button>
            <Button size="m" disabled={busy} onClick={() => void submit()}>
              {t(busy ? 'adminSaving' : 'cdkImport')}
            </Button>
          </div>
        )
      }
    >
      {error ? <PanelError message={error} /> : null}
      {result ? (
        <>
          <div className={styles.hint}>
            {t('cdkImportDone', {
              added: result.added.length,
              duplicates: result.duplicates.length,
              invalid: result.invalid.length,
            })}
          </div>
          {result.added.length > 0 ? (
            <label className={styles.field}>
              <span className={styles.fieldLabel}>{t('cdkCopyAdded')}</span>
              <textarea
                className={styles.textarea}
                readOnly
                rows={Math.min(12, Math.max(4, result.added.length))}
                value={result.added.join('\n')}
              />
            </label>
          ) : null}
          {result.duplicates.length > 0 ? (
            <div className={styles.hint}>
              {t('cdkImportDuplicates', { list: result.duplicates.join('、') })}
            </div>
          ) : null}
          {result.invalid.length > 0 ? (
            <div className={styles.hint}>
              {t('cdkImportInvalid', { list: result.invalid.join('、') })}
            </div>
          ) : null}
        </>
      ) : (
        <>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('cdkFieldCodes')}</span>
            <textarea
              className={styles.textarea}
              rows={6}
              placeholder={'XXXX-XXXX-XXXX\nXXXX-XXXX-XXXX'}
              value={codes}
              onChange={(event) => setCodes(event.target.value)}
            />
            <span className={styles.hint}>{t('cdkFieldCodesHint')}</span>
          </label>
          <div className={styles.inlineFields}>
            <NumberField
              label={t('cdkFieldAmount')}
              hint={t('cdkFieldAmountHint')}
              value={amount}
              onChange={setAmount}
            />
            <NumberField
              label={t('cdkFieldMaxUses')}
              hint={t('cdkFieldMaxUsesHint')}
              value={maxUses}
              onChange={setMaxUses}
            />
          </div>
          <TextField
            label={t('cdkFieldExpires')}
            hint={t('cdkFieldExpiresHint')}
            value={expires}
            placeholder="2099-12-31"
            onChange={setExpires}
          />
        </>
      )}
    </Modal>
  )
}