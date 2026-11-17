/**
 * 运行期配置：构建产物根目录的 config.json（无哈希、随包发布），
 * 允许部署/打包时只改配置不重新构建；localStorage 覆盖项优先级最高，便于调试切换服务器。
 * 优先级：localStorage(flyai-runtime-config) > config.json > 内置默认。
 *
 * 只保留后端地址；模型与密钥统一由后端配置（admin 维护），前端不再持有 DeepSeek Key。
 */
export type RuntimeConfig = {
  /** FlyAI 后端地址；空字符串表示同源（开发走 devServer 代理、部署走 FastAPI 静态托管） */
  apiBase: string
}

const BUILTIN_DEFAULTS: RuntimeConfig = {
  apiBase: '',
}

/** localStorage 覆盖项的键名，值为 RuntimeConfig 的局部 JSON */
export const CONFIG_OVERRIDE_KEY = 'flyai-runtime-config'

let current: RuntimeConfig = cloneConfig(BUILTIN_DEFAULTS)

function cloneConfig(config: RuntimeConfig): RuntimeConfig {
  return { apiBase: config.apiBase }
}

/** 仅合并已知字段（apiBase），未知字段忽略 */
function mergeConfig(
  base: RuntimeConfig,
  patch: Partial<RuntimeConfig> | null | undefined
): RuntimeConfig {
  const merged = cloneConfig(base)
  if (!patch || typeof patch !== 'object') {
    return merged
  }
  if (typeof patch.apiBase === 'string') {
    merged.apiBase = patch.apiBase
  }
  return merged
}

/** 应用启动时调用一次；config.json 缺失（404）或离线时静默回退到内置默认 */
export const loadRuntimeConfig = async (): Promise<RuntimeConfig> => {
  try {
    // 相对路径指向 index.html 同目录
    const res = await fetch('config.json', { cache: 'no-store' })
    if (res.ok) {
      current = mergeConfig(BUILTIN_DEFAULTS, (await res.json()) as Partial<RuntimeConfig>)
    }
  } catch {
    // 离线 / Capacitor 本地协议异常等：保留内置默认
  }
  try {
    const raw = localStorage.getItem(CONFIG_OVERRIDE_KEY)
    if (raw) {
      current = mergeConfig(current, JSON.parse(raw) as Partial<RuntimeConfig>)
    }
  } catch {
    // 覆盖项损坏时忽略，不阻断启动
  }
  return current
}

export const getConfig = (): RuntimeConfig => current

/** 去掉尾部斜杠后的后端地址；apiBase 为空时返回空串，请求即同源相对路径 */
export const getApiBase = (): string => current.apiBase.replace(/\/+$/, '')