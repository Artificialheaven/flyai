import clsx from 'clsx'
import { IconMicFill16, IconMicOutline16 } from '../../components/base/icons.js'
import { Tooltip } from '../../components/base/Tooltip.js'
import type { VoiceInputStatus } from './useVoiceInput.js'
import styles from './VoiceButton.module.css'

/**
 * 语音输入按钮。录音中为两层圆：外层半透明光圈呼吸 + 内层实心品牌色；
 * 按钮左侧显示「录音中 Ns」，窄屏不展示读秒。
 */
export const VoiceButton = ({
  status,
  disabled = false,
  seconds,
  onClick,
}: {
  status: VoiceInputStatus
  /** 非安全上下文中按钮置灰，tooltip 提示录音需要 HTTPS */
  disabled?: boolean
  seconds: number
  onClick: () => void
}) => {
  const recording = status === 'recording'
  const label = disabled
    ? '语音输入需要 HTTPS 安全上下文'
    : recording
      ? '结束录音并转写'
      : status === 'transcribing'
        ? '取消转写'
        : '语音输入'

  return (
    <>
      {recording ? <span className={styles.timer}>录音中 {seconds}s</span> : null}
      <Tooltip content={label}>
        <button
          type="button"
          className={clsx(styles.button, recording && styles.recording, disabled && styles.disabled)}
          aria-label={label}
          aria-pressed={recording}
          aria-disabled={disabled}
          onClick={() => {
            if (!disabled) {
              onClick()
            }
          }}
        >
          {recording ? (
            <span className={styles.active}>
              <span className={styles.halo} />
              <span className={styles.core} />
              <span className={styles.outline}>
                <IconMicOutline16 />
              </span>
              <span className={styles.glyph}>
                <IconMicFill16 />
              </span>
            </span>
          ) : status === 'transcribing' ? (
            <span className={styles.loading} />
          ) : (
            <IconMicOutline16 />
          )}
        </button>
      </Tooltip>
    </>
  )
}
