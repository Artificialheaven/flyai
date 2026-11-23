/**
 * 卡片写入进度条：变量/世界书/角色字段同步到会话副本时显示。
 * 不拦截操作（pointer-events: none），只做非阻塞反馈。
 */

import styles from './CardBusyBar.module.css'

export const CardBusyBar = ({ label }: { label?: string }) => (
  <div
    className={styles.bar}
    role="progressbar"
    aria-busy="true"
    aria-label={label || undefined}
  >
    <span className={styles.fill} />
    {label ? <span className={styles.label}>{label}</span> : null}
  </div>
)
