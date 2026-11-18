import { useLayoutEffect } from 'react'
import type { CSSProperties } from 'react'
import { assetUrl } from '../../services/flyai/client.js'
import { applyBackgroundPresentation, useBackground } from '../../services/background.js'
import { deviceKind } from '../../services/device.js'
import styles from './BackgroundLayer.module.css'

/**
 * 应用背景图层：铺在 .frame 内所有界面之下（z-index:-1 + .frame isolation）。
 * 只渲染当前设备（电脑/手机）的背景；theme 模式不渲染任何东西。
 */
export const BackgroundLayer = () => {
  const bg = useBackground((state) => state[deviceKind])

  // 首帧前设置 body[data-app-bg]，让侧栏/顶栏的透明变量即时生效
  useLayoutEffect(() => {
    applyBackgroundPresentation()
  }, [bg])

  if (bg.mode === 'theme') {
    return null
  }

  const style: CSSProperties = {
    // 用户可调的背景不透明度：100=完全不透明，0=完全看不见（露出主题底色）
    opacity: bg.opacity / 100,
    ...(bg.mode === 'image'
      ? {
          backgroundColor: bg.color || undefined,
          backgroundImage: `url("${assetUrl(bg.image).replace(/"/g, '%22')}")`,
        }
      : { backgroundColor: bg.color }),
  }

  return (
    <div
      aria-hidden="true"
      className={bg.mode === 'image' ? `${styles.layer} ${styles.image}` : styles.layer}
      style={style}
    />
  )
}
