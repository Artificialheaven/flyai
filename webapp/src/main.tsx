import { createRoot } from 'react-dom/client'
import { App } from './app/App.js'
import { setupTheme } from './app/theme.js'
import { loadRuntimeConfig } from './services/runtimeConfig.js'
import { useAuth } from './stores/auth.js'
import './app/theme.css'
import './app/global.css'

setupTheme()

/** 先取运行期配置，再恢复登录态，最后首屏渲染，避免登录页闪烁 */
const bootstrap = async () => {
  await loadRuntimeConfig()
  await useAuth.getState().restore()
  createRoot(document.getElementById('root') as HTMLElement).render(<App />)
}

void bootstrap()
