import { useEffect, useState, type FormEvent } from 'react'
import { useTranslate } from '../../i18n/index.js'
import { FlyApiError } from '../../services/flyai/client.js'
import {
  fetchRegisterInfo,
  resetPassword,
  sendEmailCode,
  useAuth,
  type RegisterInfo,
} from '../../services/flyai/auth.js'
import {
  CONFIG_OVERRIDE_KEY,
  getApiBase,
  isDebugRuntime,
} from '../../services/flyai/runtimeConfig.js'
import { Button } from '../base/Button.js'
import styles from './LoginView.module.css'

type Mode = 'login' | 'register' | 'forgot'

const NO_EMAIL_INFO: RegisterInfo = {
  // 探测失败/旧服务器没有该字段时，保守地继续要求邀请码
  invite_required: true,
  email_required: false,
  password_reset_enabled: false,
}

/** 登录页：沿用 chat-demo 的配色与圆角按钮；注册按后台开关决定是否要邀请码，开启邮件验证时还需邮箱验证码 */
export const LoginView = () => {
  const t = useTranslate()
  const login = useAuth((state) => state.login)
  const register = useAuth((state) => state.register)
  const [mode, setMode] = useState<Mode>('login')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [email, setEmail] = useState('')
  const [emailCode, setEmailCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [sendingCode, setSendingCode] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // 探测失败时按「未开启邮件验证」处理，保证旧部署仍可正常登录
  const [registerInfo, setRegisterInfo] = useState<RegisterInfo>(NO_EMAIL_INFO)
  // 登录页显示实际生效的后端地址：连不上主机时先确认 APK 到底在连哪
  const apiBase = getApiBase()
  const debugRuntime = isDebugRuntime()
  const hasServerOverride =
    debugRuntime &&
    (() => {
      try {
        return localStorage.getItem(CONFIG_OVERRIDE_KEY) !== null
      } catch {
        return false
      }
    })()

  useEffect(() => {
    let cancelled = false
    fetchRegisterInfo()
      .then((info) => {
        if (!cancelled) {
          setRegisterInfo(info)
        }
      })
      .catch(() => {
        // 能力探测失败不阻断登录流程
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 发送验证码后的本地倒计时（也用于禁用重发按钮）
  useEffect(() => {
    if (countdown <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [countdown])

  const inviteRequired = registerInfo.invite_required
  const emailRequired = registerInfo.email_required
  // 忘记密码仅在邮件服务已完整配置（可发信）时展示
  const canResetPassword = registerInfo.password_reset_enabled
  const needsEmail = mode === 'forgot' || (mode === 'register' && emailRequired)

  const resetServerOverride = () => {
    try {
      localStorage.removeItem(CONFIG_OVERRIDE_KEY)
    } catch {
      // 本地存储不可用时忽略
    }
    window.location.reload()
  }

  const handleSendCode = async () => {
    if (sendingCode || countdown > 0) {
      return
    }
    setError(null)
    setNotice(null)
    if (!email.trim()) {
      setError(t('loginEmailRequired'))
      return
    }
    setSendingCode(true)
    try {
      const data = await sendEmailCode(email.trim(), mode === 'forgot' ? 'reset' : 'register')
      const seconds = data.retry_after && data.retry_after > 0 ? data.retry_after : 60
      setCountdown(seconds)
      setNotice(t('loginCodeSent'))
    } catch (err) {
      setError(err instanceof FlyApiError ? err.message : t('commonNetworkErrorToast'))
    } finally {
      setSendingCode(false)
    }
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) {
      return
    }
    setError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      if (mode === 'login') {
        await login(name.trim(), password)
      } else if (mode === 'register') {
        await register(
          name.trim(),
          password,
          inviteRequired ? inviteCode.trim() : '',
          emailRequired ? email.trim() : '',
          emailRequired ? emailCode.trim() : ''
        )
      } else {
        const result = await resetPassword(email.trim(), emailCode.trim(), password)
        // 重置成功：回登录页并预填用户名，密码需重新输入
        setMode('login')
        setName(result.name)
        setPassword('')
        setEmailCode('')
        setCountdown(0)
        setNotice(t('loginResetSuccess'))
        setSubmitting(false)
        return
      }
    } catch (err) {
      setError(err instanceof FlyApiError ? err.message : t('commonNetworkErrorToast'))
      setSubmitting(false)
    }
  }

  const switchMode = () => {
    const next: Mode = mode === 'login' ? 'register' : 'login'
    setMode(next)
    // 密码、验证码与邀请码不跨模式复用，避免注册页残留登录失败的密码
    setError(null)
    setNotice(null)
    setPassword('')
    setInviteCode('')
    setEmailCode('')
    setCountdown(0)
  }

  const goForgot = () => {
    setMode('forgot')
    setError(null)
    setNotice(null)
    setPassword('')
    setInviteCode('')
    setEmailCode('')
    setCountdown(0)
  }

  const subtitle =
    mode === 'login'
      ? t('loginSubtitle')
      : mode === 'register'
        ? t('loginRegisterSubtitle')
        : t('loginResetSubtitle')

  const submitLabel = (() => {
    if (mode === 'login') {
      return submitting ? t('loginSubmitting') : t('loginButton')
    }
    if (mode === 'register') {
      return submitting ? t('loginRegisterSubmitting') : t('loginRegisterButton')
    }
    return submitting ? t('loginResetSubmitting') : t('loginResetButton')
  })()

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit}>
        <div className={styles.brand}>{t('appName')}</div>
        <div className={styles.subtitle}>{subtitle}</div>
        {mode === 'forgot' ? (
          <label className={styles.field}>
            <span className={styles.label}>{t('loginEmail')}</span>
            <input
              className={styles.input}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder={t('loginEmailPlaceholder')}
              autoComplete="email"
              autoFocus
              required
            />
            <span className={styles.hint}>{t('loginResetGenericHint')}</span>
          </label>
        ) : (
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
        )}
        {mode === 'register' && emailRequired ? (
          <label className={styles.field}>
            <span className={styles.label}>{t('loginEmail')}</span>
            <input
              className={styles.input}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder={t('loginEmailPlaceholder')}
              autoComplete="email"
              required
            />
          </label>
        ) : null}
        {needsEmail ? (
          <div className={styles.field}>
            <span className={styles.label}>{t('loginEmailCode')}</span>
            <div className={styles.codeRow}>
              <input
                className={styles.input}
                value={emailCode}
                onChange={(event) => setEmailCode(event.target.value)}
                placeholder={t('loginEmailCodePlaceholder')}
                inputMode="numeric"
                maxLength={6}
                autoComplete="one-time-code"
                required
              />
              <Button
                variant="outlined"
                size="m"
                className={styles.codeButton}
                disabled={sendingCode || countdown > 0 || submitting}
                onClick={handleSendCode}
              >
                {countdown > 0
                  ? t('loginResendIn', { seconds: countdown })
                  : sendingCode
                    ? t('loginSendingCode')
                    : t('loginSendCode')}
              </Button>
            </div>
          </div>
        ) : null}
        <label className={styles.field}>
          <span className={styles.label}>
            {mode === 'forgot' ? t('loginNewPassword') : t('loginPassword')}
          </span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
          />
        </label>
        {mode === 'register' && inviteRequired ? (
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
        {notice && !error ? (
          <div className={styles.notice} role="status">
            {notice}
          </div>
        ) : null}
        <Button type="submit" variant="primary" size="l" block disabled={submitting}>
          {submitLabel}
        </Button>
        <div className={styles.modeSwitch}>
          {mode === 'login' ? (
            <>
              {canResetPassword ? (
                <button
                  type="button"
                  className={styles.modeLink}
                  onClick={goForgot}
                  disabled={submitting}
                >
                  {t('loginForgotPassword')}
                </button>
              ) : null}
              <button
                type="button"
                className={styles.modeLink}
                onClick={switchMode}
                disabled={submitting}
              >
                {t('loginRegisterLink')}
              </button>
            </>
          ) : (
            <button
              type="button"
              className={styles.modeLink}
              onClick={switchMode}
              disabled={submitting}
            >
              {t('loginBackToLogin')}
            </button>
          )}
        </div>
        <div className={styles.serverInfo}>
          <div className={styles.serverLine}>
            <span className={styles.serverLabel}>{t('loginServerLabel')}</span>
            <span className={styles.serverValue}>
              {apiBase || t('loginServerSameOrigin')}
            </span>
          </div>
          {debugRuntime && hasServerOverride ? (
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
