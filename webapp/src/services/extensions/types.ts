export type ExtensionCapability =
  | 'chat.read'
  | 'chat.write'
  | 'variables.read'
  | 'variables.write'
  | 'worldbook.read'
  | 'worldbook.write'
  | 'generate.quiet'
  | 'generate.raw'
  | 'prompt.inject'
  | 'settings'
  | 'storage'
  | 'net'
  | 'slash.register'
  | 'slash.execute'
  | 'ui'

export type ExtensionManifest = {
  name: string
  display_name: string
  js: string
  css: string
  version: string
  author: string
  home_page: string
  loading_order: number
  requires: string[]
  dependencies: string[]
  hooks: Record<string, string>
  minimum_client_version: string
  flyai: {
    compat?: 'logic' | 'panel' | 'ui'
    host_api?: number
    capabilities?: ExtensionCapability[]
    network?: { allowed_hosts?: string[] }
    panel?: { type: 'schema' | 'iframe'; path?: string }
  }
}

export type CompatModule = { path: string; status: 'ok' | 'partial' | 'missing'; missing: string[] }
export type CompatReport = {
  level: 'L1' | 'L2' | 'L3'
  missingModules: string[]
  missingSymbols: Array<{ module: string; symbol: string }>
  modules: CompatModule[]
  uiRisk: string[]
  filesScanned: number
  declaredCompat: boolean
}

export type ExtensionItem = {
  name: string
  display_name: string
  version: string
  author: string
  loading_order: number
  compat: 'logic' | 'panel' | 'ui'
  capabilities: ExtensionCapability[]
  hooks: string[]
  installed_at: string
  sha256: string
  enabled: boolean
  order: number
  loadable: boolean
  compat_level: 'L1' | 'L2' | 'L3'
  missing_modules: number
  missing_symbols: number
  ui_risk: string[]
}

export type ExtensionDetail = {
  manifest: ExtensionManifest
  summary: ExtensionItem
  record: { name: string; version: string; sha256: string; installed_at: string }
  compat: CompatReport | null
  settings: Record<string, unknown>
  enabled: boolean
  order: number
}

export type ExtensionHostState = {
  name: string
  baseUrl: string
  js: string
  css: string
  manifest: ExtensionManifest
  settings: Record<string, unknown>
  capabilities: ExtensionCapability[]
  order: number
}

export type InstallResult = {
  ok: boolean
  extension: ExtensionItem
  compat: CompatReport
  warnings: string[]
}

export type ExtensionPanel =
  | { type: 'schema'; path: string; url: string; schema: Record<string, unknown> }
  | { type: 'iframe'; path: string; url: string }
