import { useEffect, useState } from 'react'
import { Toast } from '../components/base/Toast.js'
import { LoginView } from '../features/auth/LoginView.js'
import { ChatView } from '../features/chat/ChatView.js'
import { LoreView } from '../features/lore/LoreView.js'
import { SessionSidebar } from '../features/sidebar/SessionSidebar.js'
import { useAuth } from '../stores/auth.js'
import { useIsMobile, useMobileViewport, usePreventPageScroll } from './viewport.js'
import { navigate, useHashRoute } from './router.js'
import styles from './App.module.css'

/** 登录后主布局：桌面端固定侧栏，移动端抽屉式侧栏 */
const Shell = () => {
  const isMobile = useIsMobile()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const route = useHashRoute()
  const frameRef = useMobileViewport(isMobile)
  usePreventPageScroll(isMobile)

  // 桌面端永远展示侧栏；从移动端切回桌面时收起抽屉
  useEffect(() => {
    if (!isMobile) {
      setSidebarOpen(false)
    }
  }, [isMobile])

  return (
    <div ref={frameRef} className={styles.frame}>
      <div className={styles.body}>
        {isMobile ? (
          <>
            <div
              className={`${styles.backdrop} ${sidebarOpen ? styles.backdropOpen : ''}`}
              onClick={() => setSidebarOpen(false)}
            />
            <nav className={`${styles.drawer} ${sidebarOpen ? styles.drawerOpen : ''}`}>
              <SessionSidebar onNavigate={() => setSidebarOpen(false)} />
            </nav>
          </>
        ) : (
          <nav className={styles.sidebarSlot}>
            <SessionSidebar />
          </nav>
        )}
        {route.startsWith('/lore') ? (
          <LoreView
            onOpenSidebar={() => setSidebarOpen(true)}
            onBack={() => navigate('/')}
          />
        ) : (
          <ChatView onOpenSidebar={() => setSidebarOpen(true)} />
        )}
      </div>
      <Toast />
    </div>
  )
}

export const App = () => {
  const ready = useAuth((state) => state.ready)
  const token = useAuth((state) => state.token)
  const route = useHashRoute()

  // 已登录却停在登录页时回首页
  useEffect(() => {
    if (ready && token && route.startsWith('/login')) {
      navigate('/')
    }
  }, [ready, token, route])

  if (!ready) {
    return null
  }
  if (!token) {
    return (
      <>
        <LoginView />
        <Toast />
      </>
    )
  }
  return <Shell />
}
