// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { HOST_GLOBAL_SOURCE } from './runtime/hostShims.js'
import { EXTENSION_MODULES, EXTENSION_MODULE_EXPORTS } from './runtime/modules.js'
import { buildExtensionDoc } from './hostDoc.js'
import { buildPanelDoc } from './panelDoc.js'
import { useExtensionWidgets } from './widgets.js'
import { hasCapability, isExtMessage, messageTooLarge } from './hostProtocol.js'
import type { ExtensionHostState } from './types.js'

const state: ExtensionHostState = {
  name: 'fixture',
  baseUrl: '/scripts/extensions/third-party/fixture/',
  js: 'index.js',
  css: 'style.css',
  manifest: {
    name: 'fixture',
    display_name: 'Fixture',
    js: 'index.js',
    css: 'style.css',
    version: '1.0.0',
    author: '',
    home_page: '',
    loading_order: 100,
    requires: [],
    dependencies: [],
    hooks: { activate: 'init' },
    minimum_client_version: '',
    flyai: { compat: 'logic', capabilities: ['chat.read', 'settings'] },
  },
  settings: { a: 1 },
  capabilities: ['chat.read', 'settings'],
  order: 100,
}

const snapshot = {
  chat_id: 's1',
  characters: [],
  chat: [],
  chat_metadata: {},
  name2: 'Char',
  user_name: 'User',
  user_avatar: '',
  character_avatar: '',
  is_mobile: false,
  max_prompt_tokens: 8192,
  variables: {},
  extension_names: [],
  context: {},
}

describe('extension host protocol', () => {
  it('validates namespace/version and size', () => {
    expect(isExtMessage({ v: 1, ns: 'flyai-ext', type: 'ready' })).toBe(true)
    expect(isExtMessage({ v: 2, ns: 'flyai-ext', type: 'ready' })).toBe(false)
    expect(isExtMessage({ v: 1, ns: 'other', type: 'ready' })).toBe(false)
    expect(messageTooLarge({ data: 'x'.repeat(300 * 1024) })).toBe(true)
  })

  it('checks capability declarations', () => {
    expect(hasCapability(['chat.read'], 'chat.getMessages')).toBe(true)
    expect(hasCapability([], 'chat.getMessages')).toBe(false)
    expect(hasCapability([], 'ui.toast')).toBe(true)
    expect(hasCapability(['ui'], 'ui.widget.register')).toBe(true)
    expect(hasCapability([], 'ui.widget.register')).toBe(false)
  })
})

describe('extension module registry', () => {
  it('exports every declared symbol', () => {
    for (const [path, exports] of Object.entries(EXTENSION_MODULE_EXPORTS)) {
      const source = EXTENSION_MODULES[path]
      expect(source, path).toBeTruthy()
      for (const name of exports) {
        const declared =
          source.includes(`export const ${name}`) ||
          source.includes(`export function ${name}`) ||
          source.includes(`export class ${name}`) ||
          source.includes(`export let ${name}`) ||
          new RegExp(`export\\s*\\{[^}]*\\b${name}\\b`).test(source)
        expect(declared, `${path} -> ${name}`).toBe(true)
      }
    }
  })

  it('covers the P0 modules required by extensions', () => {
    for (const path of [
      'script.js',
      'scripts/utils.js',
      'scripts/extensions.js',
      'scripts/popup.js',
      'scripts/constants.js',
      'scripts/i18n.js',
      'scripts/secrets.js',
      'scripts/slash-commands/SlashCommandParser.js',
    ]) {
      expect(EXTENSION_MODULES[path], path).toBeTruthy()
    }
    expect(EXTENSION_MODULES['script.js']).toContain('export const eventSource')
    expect(EXTENSION_MODULES['script.js']).toContain('export const event_types')
  })
})

describe('host document', () => {
  it('builds import map, CSP and absolute entry', () => {
    const doc = buildExtensionDoc(state, snapshot, 'https://app.example')
    expect(doc).toContain('https://app.example/script.js')
    expect(doc).toContain('data:text/javascript;base64,')
    expect(doc).toContain("connect-src 'none'")
    expect(doc).toContain('import("https://app.example/scripts/extensions/third-party/fixture/index.js")')
    expect(doc).not.toContain('allow-same-origin')
  })
})

describe('extension globals', () => {
  it('event bus waits async handlers and slash registers/executes', async () => {
    new Function(HOST_GLOBAL_SOURCE)()
    const win = window as unknown as Record<string, any>
    const calls: string[] = []
    win.eventSource.on('X', async () => {
      await Promise.resolve()
      calls.push('async')
    })
    await win.eventSource.emitAndWait('X')
    expect(calls).toEqual(['async'])

    const host = win.__FLYAI_EXT_HOST__
    const command = host.slash.SlashCommand.fromProps({
      name: 'hello',
      helpString: 'say hi',
      callback: (_named: unknown, unnamed: string[]) => `hi ${unnamed[0] || ''}`.trim(),
    })
    host.slash.SlashCommandParser.addCommandObject(command)
    await expect(host.executeSlash('/hello world')).resolves.toBe('hi world')
    expect(await host.executeSlash('/hello | /hello again')).toBe('hi again')
  })
})


describe('L2 panel and widgets', () => {
  it('wraps panel.html with host shims and settings', () => {
    const doc = buildPanelDoc(
      '<html><head><style>.x{}</style></head><body><button id="b">hi</button></body></html>',
      {
        state,
        settings: { color: 'red' },
        snapshot: { chat: [] },
      },
      'https://app.example'
    )
    expect(doc).toContain('<button id="b">hi</button>')
    expect(doc).toContain('.x{}')
    expect(doc).toContain('window.__FLYAI_EXT_HOST__')
    expect(doc).toContain('connect-src \'none\'')
    expect(doc).toContain('"color":"red"')
  })

  it('registers and replaces message widgets', () => {
    const store = useExtensionWidgets.getState()
    store.clearAll()
    store.register({ ext: 'a', slot: 'message', id: 'w', label: 'Do', title: '', command: '/do' })
    store.register({ ext: 'b', slot: 'message', id: 'w', label: 'Other', title: '', command: '/x' })
    expect(useExtensionWidgets.getState().widgets).toHaveLength(2)
    store.register({ ext: 'a', slot: 'message', id: 'w', label: 'Do2', title: '', command: '/do' })
    expect(useExtensionWidgets.getState().widgets.filter((w) => w.ext === 'a')).toHaveLength(1)
    expect(useExtensionWidgets.getState().widgets.find((w) => w.ext === 'a')?.label).toBe('Do2')
    store.clearExt('a')
    expect(useExtensionWidgets.getState().widgets.map((w) => w.ext)).toEqual(['b'])
  })
})
