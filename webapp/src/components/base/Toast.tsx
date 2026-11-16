import clsx from 'clsx'
import { IconToastError16 } from './icons.js'
import { useToast } from './toastState.js'
import styles from './Toast.module.css'

export const Toast = () => {
  const { id, message, error } = useToast()
  if (!message) {
    return null
  }
  return (
    <div key={id} className={clsx(styles.toast, error && styles.error)} role="status">
      {error ? (
        <span className={styles.icon}>
          <IconToastError16 />
        </span>
      ) : null}
      <span className={styles.content}>{message}</span>
    </div>
  )
}
