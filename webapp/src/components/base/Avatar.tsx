import clsx from 'clsx'
import { assetUrl } from '../../services/api/client.js'
import styles from './Avatar.module.css'

/** 角色/用户头像：有图用图，无图显示名称首字（替代已移除的 default-avatar.svg） */
export const Avatar = ({
  src,
  name,
  size = 36,
  className,
}: {
  src?: string | null
  name?: string | null
  size?: number
  className?: string
}) => {
  const url = assetUrl(src)
  const letter = (name || '?').trim().charAt(0).toUpperCase() || '?'
  return (
    <span
      className={clsx(styles.avatar, className)}
      style={{ width: size, height: size, fontSize: Math.max(12, Math.round(size * 0.4)) }}
    >
      {url ? <img className={styles.image} src={url} alt="" /> : <span className={styles.letter}>{letter}</span>}
    </span>
  )
}