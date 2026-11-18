import clsx from 'clsx'
import { useTranslate } from '../../../i18n/index.js'
import { Select } from '../../base/Select.js'
import styles from './AdminPanels.module.css'

/** 管理面板共用的错误文案、空态、开关与「跟随全局」三态选择。 */

export const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error)

export const PanelError = ({ message }: { message: string }) => (
  <div className={styles.error}>{message}</div>
)

export const PanelEmpty = ({ text }: { text: string }) => (
  <div className={styles.empty}>{text}</div>
)

export const Toggle = ({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  disabled?: boolean
}) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    className={clsx(styles.toggle, checked && styles.toggleOn)}
    disabled={disabled}
    onClick={() => onChange(!checked)}
  >
    <span className={styles.toggleKnob} />
  </button>
)

/** 数字输入：value 用字符串保存，空串由调用方解释为「跟随默认」/「清空」 */
export const NumberField = ({
  label,
  hint,
  value,
  placeholder,
  disabled = false,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  disabled?: boolean
  onChange: (value: string) => void
}) => (
  <label className={styles.field}>
    <span className={styles.fieldLabel}>{label}</span>
    <input
      className={styles.input}
      value={value}
      placeholder={placeholder}
      inputMode="numeric"
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
    {hint ? <span className={styles.hint}>{hint}</span> : null}
  </label>
)

/** 文本输入：与 NumberField 同版式，用于地址 / 名称 / 时段等字符串配置 */
export const TextField = ({
  label,
  hint,
  value,
  placeholder,
  type = 'text',
  disabled = false,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  type?: 'text' | 'password' | 'email'
  disabled?: boolean
  onChange: (value: string) => void
}) => (
  <label className={styles.field}>
    <span className={styles.fieldLabel}>{label}</span>
    <input
      className={styles.input}
      type={type}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
    {hint ? <span className={styles.hint}>{hint}</span> : null}
  </label>
)

/** 多行文本：公告正文等需要保留换行的配置 */
export const TextAreaField = ({
  label,
  hint,
  value,
  placeholder,
  rows = 8,
  disabled = false,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  rows?: number
  disabled?: boolean
  onChange: (value: string) => void
}) => (
  <label className={styles.field}>
    <span className={styles.fieldLabel}>{label}</span>
    <textarea
      className={styles.textarea}
      value={value}
      placeholder={placeholder}
      rows={rows}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
    {hint ? <span className={styles.hint}>{hint}</span> : null}
  </label>
)

/** 左标签说明 + 右开关的整行设置 */
export const SwitchRow = ({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string
  desc?: string
  checked: boolean
  onChange: (checked: boolean) => void
}) => (
  <div className={styles.row}>
    <div className={styles.rowText}>
      <div className={styles.rowLabel}>{label}</div>
      {desc ? <div className={styles.rowDesc}>{desc}</div> : null}
    </div>
    <Toggle checked={checked} label={label} onChange={onChange} />
  </div>
)

/** 左标签说明 + 右「跟随全局/开/关」三态选择 */
export const TriRow = ({
  label,
  desc,
  value,
  globalValue,
  onChange,
}: {
  label: string
  desc?: string
  value: boolean | null
  globalValue?: boolean
  onChange: (value: boolean | null) => void
}) => (
  <div className={styles.row}>
    <div className={styles.rowText}>
      <div className={styles.rowLabel}>{label}</div>
      {desc ? <div className={styles.rowDesc}>{desc}</div> : null}
    </div>
    <TriSelect
      value={value}
      globalValue={globalValue}
      ariaLabel={label}
      onChange={onChange}
    />
  </div>
)

export type TriValue = 'inherit' | 'on' | 'off'

export const toTri = (value: boolean | null): TriValue =>
  value === null ? 'inherit' : value ? 'on' : 'off'

export const fromTri = (value: TriValue): boolean | null =>
  value === 'inherit' ? null : value === 'on'

/** 个人覆盖字段：null（跟随全局）/ 开启 / 关闭；globalValue 用于提示当前全局默认 */
export const TriSelect = ({
  value,
  globalValue,
  ariaLabel,
  onChange,
}: {
  value: boolean | null
  globalValue?: boolean
  ariaLabel: string
  onChange: (value: boolean | null) => void
}) => {
  const t = useTranslate()
  const globalLabel =
    globalValue === undefined ? t('adminInherit') : globalValue ? t('adminOn') : t('adminOff')
  return (
    <Select<TriValue>
      value={toTri(value)}
      ariaLabel={ariaLabel}
      options={[
        {
          value: 'inherit',
          label: globalValue === undefined ? globalLabel : t('adminInheritValue', { value: globalLabel }),
        },
        { value: 'on', label: t('adminOn') },
        { value: 'off', label: t('adminOff') },
      ]}
      onChange={(next) => onChange(fromTri(next))}
    />
  )
}