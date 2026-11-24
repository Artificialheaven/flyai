import { HOST_GLOBAL_SOURCE } from './runtime/hostShims.js'
import { buildExtensionImportMap } from './runtime/modules.js'
import type { ExtensionHostState } from './types.js'

export type ExtensionSnapshot = {
  chat_id: string
  characters: unknown[]
  chat: Array<Record<string, unknown>>
  chat_metadata: Record<string, unknown>
  name2: string
  user_name: string
  user_avatar: string
  character_avatar: string
  is_mobile: boolean
  max_prompt_tokens: number
  variables: Record<string, unknown>
  extension_names: string[]
  context: Record<string, unknown>
}

const escapeScriptJson = (value: unknown): string =>
  JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e')

/**
 * 构造扩展宿主 iframe 的 srcdoc。
 * - CSP 不放开外网 connect，所有网络走宿主 RPC；
 * - import map 把 ST 模块路径映射到 data: shim；
 * - 入口用绝对 URL，扩展目录内的相对 import 仍按 ST 路径解析。
 */
export const buildExtensionDoc = (
  state: ExtensionHostState,
  snapshot: ExtensionSnapshot,
  origin: string
): string => {
  const entry = `${origin}${state.baseUrl}${state.js}`
  const hook = state.manifest.hooks?.activate || ''
  const boot = {
    name: state.name,
    baseUrl: `${origin}${state.baseUrl}`,
    settings: state.settings,
    capabilities: state.capabilities,
    manifest: state.manifest,
    state: snapshot,
  }
  const csp = [
    "default-src 'none'",
    `script-src 'unsafe-inline' 'unsafe-eval' data: blob: ${origin}`,
    `style-src 'unsafe-inline' ${origin}`,
    `img-src data: blob: ${origin}`,
    `font-src data: ${origin}`,
    "connect-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ')

  const importMap = buildExtensionImportMap(origin)
  const css = state.css ? `<link rel="stylesheet" href="${origin}${state.baseUrl}${state.css}">` : ''

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
${css}
<style>html,body{margin:0;padding:0;background:transparent;color:#ddd;font:12px/1.4 system-ui,sans-serif}#flyai-ext-root{display:none}</style>
</head>
<body>
<div id="flyai-ext-root"></div>
<script>window.__FLYAI_EXT_BOOT__ = ${escapeScriptJson(boot)};</script>
<script>${HOST_GLOBAL_SOURCE}</script>
<script type="importmap">${escapeScriptJson({ imports: importMap })}</script>
<script type="module">
(async () => {
  const H = window.__FLYAI_EXT_HOST__;
  try {
    const mod = await import(${JSON.stringify(entry)});
    const hook = ${JSON.stringify(hook)};
    if (hook && typeof mod[hook] === 'function') {
      await mod[hook]();
    }
    H.ready();
  } catch (error) {
    H.error('extension load failed: ' + (error && error.message ? error.message : error));
  }
})();
</script>
</body>
</html>`
}
