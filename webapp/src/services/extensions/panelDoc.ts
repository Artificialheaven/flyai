import { HOST_GLOBAL_SOURCE } from './runtime/hostShims.js'
import type { ExtensionHostState } from './types.js'

export type PanelBoot = {
  state: ExtensionHostState
  settings: Record<string, unknown>
  snapshot: Record<string, unknown>
}

const escapeScriptJson = (value: unknown): string =>
  JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e')

/** 把扩展的 panel.html 包进宿主 shim（同一套 settings/toast RPC）。 */
export const buildPanelDoc = (html: string, boot: PanelBoot, origin: string): string => {
  let head = ''
  let body = html
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html')
    head = doc.head ? doc.head.innerHTML : ''
    body = doc.body ? doc.body.innerHTML : html
  } catch {
    // 非完整文档时按片段处理
  }
  const csp = [
    "default-src 'none'",
    `script-src 'unsafe-inline' 'unsafe-eval' data: blob: ${origin}`,
    `style-src 'unsafe-inline' ${origin}`,
    `img-src data: blob: ${origin}`,
    `font-src data: ${origin}`,
    "connect-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
  ].join('; ')
  const bootJson = {
    name: boot.state.name,
    baseUrl: `${origin}${boot.state.baseUrl}`,
    settings: boot.settings,
    capabilities: boot.state.capabilities,
    manifest: boot.state.manifest,
    state: boot.snapshot,
  }
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>html,body{margin:0;padding:0;background:transparent;color:inherit;font:13px/1.45 system-ui,sans-serif}</style>
${head}
<script>window.__FLYAI_EXT_BOOT__ = ${escapeScriptJson(bootJson)};</script>
<script>${HOST_GLOBAL_SOURCE}</script>
</head>
<body>
${body}
</body>
</html>`
}
