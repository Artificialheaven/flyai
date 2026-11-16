import { getRuntimeConfig } from '../../../services/api/lore.js'
import type { RuntimeConfigView } from '../../../types/lore.js'

/** /api/config 只拉一次：编辑器共用音色清单与默认格式要求，失败时静默降级 */
let configPromise: Promise<RuntimeConfigView | null> | null = null

export const loadRuntimeConfig = (): Promise<RuntimeConfigView | null> => {
  if (!configPromise) {
    configPromise = getRuntimeConfig().catch(() => null)
  }
  return configPromise
}