import { useState, type FormEvent } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { FlyApiError } from '../../services/flyai/client.js'
import { useAuth } from '../../services/flyai/auth.js'
import { CONFIG_OVERRIDE_KEY, getApiBase } from '../../services/flyai/runtimeConfig.js'
import { Button } from '../base/Button.js'
import styles from './LoginView.module.css'

type Mode = 'login' | 'register'

/** 登录页：沿用 chat-demo 的配色与圆角按钮；注册模式必须填写邀请码 */
export const LoginView = () => {
  const t = useTranslate()
  const login = useAuth((state) => state.login)
  const register = useAuth((state) => state.register)
  const [mode, setMode] = useState<Mode>('login')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 登录页显示实际生效的后端地址：连不上主机时先确认 APK 到底在连哪
  const apiBase = getApiBase()
  const hasServerOverride = (() => {
    try {
      return localStorage.getItem(CONFIG_OVERRIDE_KEY) !== null
    } catch {
      return false
    }
  })()

  const resetServerOverride = () => {
    try {
      localStorage.removeItem(CONFIG_OVERRIDE_KEY)
    } catch {
      // 本地存储不可用时忽略
    }
    window.location.reload()
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) {
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      if (mode === 'login') {
        await login(name.trim(), password)
      } else {
        await register(name.trim(), password, inviteCode.trim())
      }
    } catch (err) {
      setError(err instanceof FlyApiError ? err.message : t('commonNetworkErrorToast'))
      setSubmitting(false)
    }
  }

  const switchMode = () => {
    setMode(mode === 'login' ? 'register' : 'login')
    // 密码与邀请码不跨模式复用，避免注册页残留登录失败的密码
    setError(null)
    setPassword('')
    setInviteCode('')
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit}>
        <div className={styles.brand}>{t('appName')}</div>
        <div className={styles.subtitle}>
          {mode === 'login' ? t('loginSubtitle') : t('loginRegisterSubtitle')}
        </div>
        <label className={styles.field}>
          <span className={styles.label}>{t('loginUsername')}</span>
          <input
            className={styles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{t('loginPassword')}</span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
          />
        </label>
        {mode === 'register' ? (
          <label className={styles.field}>
            <span className={styles.label}>{t('loginInviteCode')}</span>
            <input
              className={styles.input}
              value={inviteCode}
              onChange={(event) => setInviteCode(event.target.value)}
              placeholder={t('loginInvitePlaceholder')}
              autoComplete="off"
              required
            />
          </label>
        ) : null}
        {error ? (
          <div className={styles.error} role="alert">
            {error}
          </div>
        ) : null}
        <Button type="submit" variant="primary" size="l" block disabled={submitting}>
          {mode === 'login'
            ? submitting
              ? t('loginSubmitting')
              : t('loginButton')
            : submitting
              ? t('loginRegisterSubmitting')
              : t('loginRegisterButton')}
        </Button>
        <div className={styles.modeSwitch}>
          <button
            type="button"
            className={styles.modeLink}
            onClick={switchMode}
            disabled={submitting}
          >
            {mode === 'login' ? t('loginRegisterLink') : t('loginBackToLogin')}
          </button>
        </div>
        <div className={styles.serverInfo}>
          <div className={styles.serverLine}>
            <span className={styles.serverLabel}>{t('loginServerLabel')}</span>
            <span className={styles.serverValue}>
              {apiBase || t('loginServerSameOrigin')}
            </span>
          </div>
          {hasServerOverride ? (
            <div className={styles.serverOverride}>
              <span>{t('loginServerOverrideHint')}</span>
              <button
                type="button"
                className={styles.serverReset}
                onClick={resetServerOverride}
              >
                {t('loginServerReset')}
              </button>
            </div>
          ) : null}
        </div>
      </form>
    </div>
  )
}