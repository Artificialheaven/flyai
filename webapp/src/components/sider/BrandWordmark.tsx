import { useId } from 'react'
import styles from './BrandWordmark.module.css'

/** 侧栏品牌字标：圆头笔画手写的 “Fly”，配几何大写 “AI”。
 *  纯矢量路径，不依赖系统字体，各平台渲染一致；渐变沿用品牌蓝色阶。 */
export const BrandWordmark = ({ label = 'FlyAI' }: { label?: string }) => {
  // useId 生成的是 ":r1:" 这类带冒号的 id，CSS url() 里去掉更稳妥
  const gradientId = `brand-${useId().replace(/:/g, '')}`
  return (
    <svg
      className={styles.wordmark}
      viewBox="0 0 90 46"
      role="img"
      aria-label={label}
      focusable="false"
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1="0"
          y1="0"
          x2="90"
          y2="0"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" style={{ stopColor: 'var(--dsw-static-blue-400)' }} />
          <stop offset="0.55" style={{ stopColor: 'var(--dsw-static-blue-450)' }} />
          <stop offset="1" style={{ stopColor: 'var(--dsw-static-blue-500)' }} />
        </linearGradient>
      </defs>
      <g className={styles.glyphs} style={{ stroke: `url(#${gradientId})` }}>
        {/* F */}
        <path d="M9 32V10h15" />
        <path d="M9 21h11" />
        {/* l */}
        <path d="M31 10v22" />
        {/* y */}
        <path d="M39 18l6 10 6-10" />
        <path d="M45 28c0 5-2 8-6 9.5" />
        {/* A */}
        <path d="M59.5 32l7-22 7 22" />
        <path d="M63 20.5h7" />
        {/* I */}
        <path d="M81.5 10v22" />
      </g>
    </svg>
  )
}
