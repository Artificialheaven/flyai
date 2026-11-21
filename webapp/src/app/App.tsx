import { useEffect } from 'react'
import { useTranslate } from '../i18n/index.js'
import { useSettings } from '../services/settings.js'
import { useIsMobile, useMobileViewport, usePreventPageScroll } from './viewport.js'
import {
  bindChatPersistence,
  initChat,
  resetChat,
  retrySaveSessions,
  selectSession,
  useChatStore,
} from '../services/chat/store.js'
import { SearchPanel } from '../components/search/SearchPanel.js'
import { closeSearchPanel, useSearchPanel } from '../components/search/searchPanelState.js'
import { closeTopOverlay } from '../components/base/overlayStack.js'
import { initNative } from '../services/native.js'
import { SessionView } from '../components/session/SessionView.js'
import { MobileHeader } from '../components/session/MobileHeader.js'
import { Welcome } from '../components/session/Welcome.js'
import { LoginView } from '../components/auth/LoginView.js'
import { useAuth } from '../services/flyai/auth.js'
import { loadRuntimeConfig } from '../services/flyai/runtimeConfig.js'
import { checkForAppUpdate, useAppUpdate } from '../services/flyai/appUpdate.js'
import { loadAnnouncement } from '../services/flyai/announcement.js'
import { UpdateDialog } from '../components/update/UpdateDialog.js'
import { AnnouncementDialog } from '../components/base/AnnouncementDialog.js'
import { SettingsDialog } from '../components/settings/SettingsDialog.js'
import { LoreDialog } from '../components/lore/LoreDialog.js'
import { NewRoomDialog } from '../components/room/NewRoomDialog.js'
import { Toast } from '../components/base/Toast.js'
import { showToast } from '../components/base/toastState.js'
import { onUploadNotice } from '../services/attachments.js'
import { Button } from '../components/base/Button.js'
import { stopReadAloud } from '../services/speech/readAloud.js'
import { CollapsedSider } from '../components/sider/CollapsedSider.js'
import { closeMobileSider, useMobileSider } from '../components/sider/mobileSiderState.js'
import { MobileSider, Sider } from '../components/sider/Sider.js'
import { BackgroundLayer } from '../components/background/BackgroundLayer.js'
import { clearBackground, loadBackground } from '../services/background.js'
import styles from './App.module.css'

export const App = () => {
  const t = useTranslate()
  const isMobile = useIsMobile()
  const locale = useSettings((state) => state.locale)
  // 移动端侧边栏以独立抽屉管理开合状态
  const siderCollapsed = useSettings((state) => state.siderCollapsed) && !isMobile
  const ready = useChatStore((state) => state.ready)
  const initError = useChatStore((state) => state.initError)
  const saveError = useChatStore((state) => Object.values(state.sessionSaveErrors).some(Boolean))
  const importingHistory = useChatStore((state) => state.importingHistory)
  const session = useChatStore((state) =>
    state.activeId !== null && Object.hasOwn(state.sessionCache, state.activeId)
      ? state.sessionCache[state.activeId]
      : undefined
  )
  const sessionLoadError = useChatStore((state) =>
    state.activeId === null ? false : state.sessionLoadErrors[state.activeId] === true
  )
  const frameRef = useMobileViewport(isMobile)
  const streaming = useChatStore((state) =>
    state.activeId === null ? false : Object.hasOwn(state.streaming, state.activeId)
  )
  const activeId = useChatStore((state) => state.activeId)
  const authReady = useAuth((state) => state.ready)
  const token = useAuth((state) => state.token)
  // APK 更新弹窗优先；关闭后再显示启动公告
  const updateDialogOpen = useAppUpdate((state) => state.open)

  usePreventPageScroll(isMobile)

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  // 文件数、存储空间达到上限时，附件上只显示「上传失败」，原因用 toast 说明
  useEffect(() => onUploadNotice(showToast), [])
  useEffect(bindChatPersistence, [])

  // 搜索结果面板只属于当前会话
  useEffect(closeSearchPanel, [activeId])
  // 切换会话时停止朗读
  useEffect(stopReadAloud, [activeId])
  // 移动端选中会话、切换布局后收起抽屉
  useEffect(closeMobileSider, [activeId, isMobile])

  // APK 返回键：先关最上层弹窗/浮层，再收搜索结果与侧边抽屉，都没有时退出应用
  useEffect(() => {
    initNative(() => {
      if (closeTopOverlay()) {
        return true
      }
      if (useSearchPanel.getState().entry !== null) {
        closeSearchPanel()
        return true
      }
      if (useMobileSider.getState().open) {
        closeMobileSider()
        return true
      }
      return false
    })
  }, [])

  // 服务器地址来自根目录 config.json，必须在任何请求发出前加载完成；
  // 随后并行拉取启动公告、检查 APK 更新（均静默失败，不阻塞启动）
  useEffect(() => {
    void loadRuntimeConfig().then(() => {
      // 会话列表只依赖本地 token，与 /me 校验并行，省一个启动 RTT
      if (useAuth.getState().token) {
        void initChat()
      }
      useAuth.getState().restore()
      void loadAnnouncement()
      void checkForAppUpdate()
    })
  }, [])

  // 登录态就绪后再拉取会话；登出时清空内存中的列表与缓存
  useEffect(() => {
    if (!authReady) {
      return
    }
    if (token) {
      void initChat()
      // 背景与会话并行加载：首屏先用本机缓存播种，接口返回后再校准
      void loadBackground()
    } else {
      resetChat()
      clearBackground()
    }
  }, [authReady, token])

  if (authReady && !token) {
    return (
      <>
        <LoginView />
        <UpdateDialog />
        {updateDialogOpen ? null : <AnnouncementDialog />}
        <Toast />
      </>
    )
  }

  return (
    <>
      <div ref={frameRef} className={styles.frame}>
        <BackgroundLayer />
        {isMobile ? <MobileSider /> : siderCollapsed ? null : <Sider />}
        <main className={styles.main}>
          {saveError ? (
            <div className={styles.saveError} role="alert">
              <span>{t('sessionSaveFailed')}</span>
              <Button
                variant="outlined"
                size="s"
                disabled={importingHistory}
                onClick={retrySaveSessions}
              >
                {t('retry')}
              </Button>
            </div>
          ) : null}
          <div className={styles.content}>
            {siderCollapsed ? <CollapsedSider /> : null}
            {!ready || (activeId !== null && !session) ? (
              <div className={styles.loadingView}>
                {isMobile ? <MobileHeader /> : null}
                <div className={styles.loadingState} role="status" aria-live="polite">
                  <span>
                    {(!ready ? initError : sessionLoadError)
                      ? t(!ready ? 'sessionListLoadFailed' : 'sessionLoadFailed')
                      : t('sessionLoading')}
                  </span>
                  {(!ready ? initError : sessionLoadError) ? (
                    <Button
                      variant="outlined"
                      size="m"
                      onClick={() => {
                        if (!ready) {
                          // force：mounted 时可能已发起过一次失败的列表请求
                          void initChat(true)
                        } else if (activeId !== null) {
                          selectSession(activeId)
                        }
                      }}
                    >
                      {t('retry')}
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : session ? (
              <SessionView
                session={session}
                streaming={streaming}
                siderCollapsed={siderCollapsed}
              />
            ) : (
              <Welcome />
            )}
          </div>
        </main>
        <SearchPanel />
      </div>
      <SettingsDialog />
      <LoreDialog />
      <NewRoomDialog />
      <UpdateDialog />
      {updateDialogOpen ? null : <AnnouncementDialog />}
      <Toast />
    </>
  )
}
