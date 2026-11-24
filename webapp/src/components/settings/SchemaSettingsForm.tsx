import { useMemo } from 'react'
import styles from './ExtensionPanel.module.css'

type JsonSchema = Record<string, unknown>

const propType = (prop: JsonSchema): string => String(prop.type || (prop.enum ? 'string' : 'string'))

const defaultValue = (prop: JsonSchema): unknown => {
  if (prop.default !== undefined) return prop.default
  switch (propType(prop)) {
    case 'boolean':
      return false
    case 'number':
    case 'integer':
      return 0
    case 'array':
      return []
    default:
      return ''
  }
}

/**
 * 由扩展的 panel.schema.json 渲染原生表单：不执行扩展 HTML。
 * 支持 object / string / number / integer / boolean / enum / string[]。
 */
export const SchemaSettingsForm = ({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
}) => {
  const properties = useMemo(() => {
    const props = schema.properties
    return props && typeof props === 'object' && !Array.isArray(props)
      ? (props as Record<string, JsonSchema>)
      : {}
  }, [schema])

  const read = (key: string, prop: JsonSchema): unknown =>
    value[key] !== undefined ? value[key] : defaultValue(prop)

  const write = (key: string, next: unknown) => {
    onChange({ ...value, [key]: next })
  }

  if (Object.keys(properties).length === 0) {
    return <div className={styles.meta}>panel.schema.json 没有可渲染的 properties</div>
  }

  return (
    <div className={styles.formGrid}>
      {Object.entries(properties).map(([key, prop]) => {
        const title = String(prop.title || key)
        const description = prop.description ? String(prop.description) : ''
        const type = propType(prop)
        const current = read(key, prop)
        const enumValues = Array.isArray(prop.enum) ? prop.enum.map(String) : null
        const formatTextarea =
          type === 'string' &&
          (prop.format === 'textarea' || (prop as { ui?: { widget?: string } }).ui?.widget === 'textarea' ||
            Number(prop.maxLength || 0) > 120)

        return (
          <label className={styles.field} key={key}>
            <span className={styles.fieldTitle}>{title}</span>
            {description ? <span className={styles.fieldHint}>{description}</span> : null}
            {type === 'boolean' ? (
              <input
                type="checkbox"
                checked={Boolean(current)}
                onChange={(event) => write(key, event.target.checked)}
              />
            ) : enumValues ? (
              <select
                className={styles.select}
                value={String(current ?? enumValues[0])}
                onChange={(event) => write(key, event.target.value)}
              >
                {enumValues.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : type === 'array' ? (
              <textarea
                className={styles.settingsArea}
                value={Array.isArray(current) ? current.join('\n') : String(current ?? '')}
                onChange={(event) =>
                  write(
                    key,
                    event.target.value
                      .split('\n')
                      .map((line) => line.trim())
                      .filter(Boolean)
                  )
                }
              />
            ) : type === 'number' || type === 'integer' ? (
              <input
                className={styles.input}
                type="number"
                value={Number(current ?? 0)}
                min={prop.minimum !== undefined ? Number(prop.minimum) : undefined}
                max={prop.maximum !== undefined ? Number(prop.maximum) : undefined}
                step={type === 'integer' ? 1 : 'any'}
                onChange={(event) => {
                  const parsed = type === 'integer' ? parseInt(event.target.value, 10) : Number(event.target.value)
                  write(key, Number.isFinite(parsed) ? parsed : 0)
                }}
              />
            ) : formatTextarea ? (
              <textarea
                className={styles.settingsArea}
                value={String(current ?? '')}
                onChange={(event) => write(key, event.target.value)}
              />
            ) : (
              <input
                className={styles.input}
                type="text"
                value={String(current ?? '')}
                onChange={(event) => write(key, event.target.value)}
              />
            )}
          </label>
        )
      })}
    </div>
  )
}
