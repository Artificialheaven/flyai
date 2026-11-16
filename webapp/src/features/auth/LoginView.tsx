import { useState, type FormEvent } from 'react'
import { Button } from '../../components/base/Button.js'
import { ApiError } from '../../services/api/client.js'
import { useAuth } from '../../stores/auth.js'
import { ThemeSelect } from '../../app/ThemeSelect.js'
import styles from './LoginView.module.css'

export const LoginView = () => {
  const login = useAuth((state) => state.login)
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) {
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await login(name.trim(), password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '网络异常，请稍后重试')
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.themeCorner}>
        <ThemeSelect />
      </div>
      <form className={styles.card} onSubmit={onSubmit}>
        <div className={styles.brand}>FlyAI</div>
        <div className={styles.subtitle}>登录后开启你的角色对话</div>
        <label className={styles.field}>
          <span className={styles.label}>用户名</span>
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
          <span className={styles.label}>密码</span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error ? <div className={styles.error}>{error}</div> : null}
        <Button type="submit" block disabled={submitting}>
          {submitting ? '登录中…' : '登录'}
        </Button>
      </form>
    </div>
  )
}
