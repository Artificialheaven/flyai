import vm from 'node:vm'
import { describe, expect, it } from 'vitest'
import { buildEjsTemplateSource } from './ejsRuntime.js'
import {
  buildNativeImportMapEntries,
  buildNativeModuleUrl,
  ST_NATIVE_MODULE_PATHS,
  ST_NATIVE_SNAPSHOT_SOURCE,
} from './stNative.js'

const runSnapshot = (sandbox: Record<string, unknown>) => {
  sandbox.window = sandbox
  const context = vm.createContext(sandbox)
  vm.runInContext(ST_NATIVE_SNAPSHOT_SOURCE, context)
  return context
}

const baseHost = (): Record<string, any> => ({
  SillyTavern: {
    getContext: () => ({
      chatId: 'c1',
      characterId: 0,
      name1: '士道',
      name2: '十香',
      chat: [{ mes: 'hi' }],
      chatMetadata: { integrity: 'c1' },
      characters: [{ name: '十香', avatar: 'tohka.png' }],
      tagMap: { 'tohka.png': ['精灵'] },
      extensionPrompts: {},
    }),
  },
  TavernHelper: {
    getWorldbookNames: () => ['十香'],
    getCharWorldbookNames: () => ({ primary: '十香', additional: [] }),
    getCurrentCharName: () => '十香',
    getCurrentPersonaName: () => '士道',
    getAllVariables: () => ({ stat_data: { 好感: 10 } }),
  },
})

describe('ST 原生快照', () => {
  it('把 shim 状态整理成 ST 形状并标记原生宿主可用', () => {
    const sandbox = baseHost()
    runSnapshot(sandbox)
    const native = sandbox.__FLYAI_ST_NATIVE__ as Record<string, any>
    expect(sandbox.__FLYAI_NATIVE_HOST__).toBe(true)
    expect(native.chatId).toBe('c1')
    expect(native.characterName).toBe('十香')
    expect(native.userName).toBe('士道')
    expect(native.mainApi).toBe('openai')
    expect(native.worldInfo.names).toEqual(['十香'])
    expect(native.worldInfo.charWorldName).toBe('十香')
    expect(native.extensions.names).toContain('third-party/ST-EJS')
    expect(native.tags.tagMap['tohka.png']).toEqual(['精灵'])
    // 卡内 getChatCompletionModel 对 custom 源读 custom_model。
    expect(native.oaiSettings.custom_model).toBe('flyai')
    expect(native.oaiSettings.custom_include_body).toBe('')
  })

  it('补齐 getContext 的 main_api/tagMap，不影响原有写入 API', () => {
    const sandbox = baseHost()
    const marker = () => 'ok'
    ;(sandbox.SillyTavern as Record<string, any>).getContext = (() => {
      const original = baseHost().SillyTavern.getContext
      return () => ({ ...original(), saveMetadata: marker })
    })()
    runSnapshot(sandbox)
    const context = (sandbox.SillyTavern as Record<string, any>).getContext()
    expect(context.mainApi).toBe('openai')
    expect(context.tagMap).toEqual({ 'tohka.png': ['精灵'] })
    expect(context.saveMetadata()).toBe('ok')
  })
})

describe('ST 原生模块 import map', () => {
  it('覆盖 P0 的 10 个模块路径', () => {
    const entries = buildNativeImportMapEntries()
    for (const path of ST_NATIVE_MODULE_PATHS) {
      expect(entries[`null/${path}`]).toMatch(/^data:text\/javascript,/)
    }
    expect(ST_NATIVE_MODULE_PATHS).toHaveLength(20)
    for (const specifier of [
      'flyai-chevrotain',
      'flyai-moment',
      'flyai-seedrandom',
      'flyai-droll',
    ]) {
      expect(entries[specifier]).toMatch(/^data:text\/javascript,/)
    }
  })

  it('lib.js 引入宏引擎依赖并按要求导出', () => {
    const source = decodeURIComponent(
      buildNativeModuleUrl('lib.js').slice('data:text/javascript,'.length)
    )
    expect(source).toContain("import __moment from 'flyai-moment'")
    expect(source).toContain("import __seedrandom from 'flyai-seedrandom'")
    expect(source).toContain("import * as __drollModule from 'flyai-droll'")
    expect(source).toContain("import * as __chevrotain from 'flyai-chevrotain'")
    expect(source).toContain('export const moment =')
    expect(source).toContain('export const seedrandom =')
    expect(source).toContain('export const droll =')
    expect(source).toContain('export const chevrotain = __chevrotain')
  })

  it('所有原生模块（除 lib 依赖链）都能作为 ES module 求值', async () => {
    for (const path of ST_NATIVE_MODULE_PATHS) {
      if (path === 'lib.js') continue
      const mod = (await import(buildNativeModuleUrl(path))) as Record<string, unknown>
      expect(mod && typeof mod).toBe('object')
    }
  })

  it('moments/droll/seedrandom 依赖模块自带可用的导出', () => {
    const entries = buildNativeImportMapEntries()
    const read = (key: string): string =>
      decodeURIComponent((entries[key] ?? '').slice('data:text/javascript,'.length))
    expect(read('flyai-moment')).toContain('export default globalThis.moment')
    expect(read('flyai-seedrandom')).toContain(' as default')
    expect(read('flyai-droll')).toContain(' as validate')
    expect(read('flyai-droll')).toContain(' as roll')
  })

  it('script.js 模块从快照读状态并实现预算函数', () => {
    const url = buildNativeModuleUrl('script.js')
    expect(url).toMatch(/^data:text\/javascript,/)
    const source = decodeURIComponent(url.slice('data:text/javascript,'.length))
    expect(source).toContain('export const chat =')
    expect(source).toContain('export function getCurrentChatId()')
    expect(source).toContain('export function getMaxResponseTokens()')
  })

  it('world-info.js 导出运行时检查的 12 个设置项', () => {
    const source = decodeURIComponent(
      buildNativeModuleUrl('scripts/world-info.js').slice('data:text/javascript,'.length)
    )
    for (const name of [
      'world_info_depth',
      'world_info_min_activations',
      'world_info_min_activations_depth_max',
      'world_info_budget',
      'world_info_recursive',
      'world_info_overflow_alert',
      'world_info_case_sensitive',
      'world_info_match_whole_words',
      'world_info_use_group_scoring',
      'world_info_character_strategy',
      'world_info_budget_cap',
      'world_info_max_recursion_steps',
      'DEFAULT_DEPTH',
      'world_info_include_names',
      'loadWorldInfo',
    ]) {
      expect(source).toContain(name)
    }
  })

  it('openai.js 暴露 main_api 兼容的 oai_settings', () => {
    const source = decodeURIComponent(
      buildNativeModuleUrl('scripts/openai.js').slice('data:text/javascript,'.length)
    )
    expect(source).toContain('export const oai_settings')
    expect(source).toContain('export const model_list')
  })
})

describe('EjsTemplate 兼容层', () => {
  it('compileTemplate 返回可调用函数并合并 locals', async () => {
    const sandbox = baseHost()
    runSnapshot(sandbox)
    const context = vm.createContext(sandbox)
    vm.runInContext(buildEjsTemplateSource(), context)
    const EjsTemplate = sandbox.EjsTemplate as {
      compileTemplate: (
        code: string,
        options: unknown,
        locals: Record<string, unknown>
      ) => Promise<(context: Record<string, unknown>) => Promise<string>>
      getFeatures: () => Record<string, unknown>
    }
    const locals = { char: '十香', variables: { stat_data: { 好感: 12 } } }
    const compiled = await EjsTemplate.compileTemplate(
      '你好 <%= char %>，好感 <%- variables.stat_data.好感 %>',
      { options: {} },
      locals
    )
    expect(await compiled.call(locals, locals)).toBe('你好 十香，好感 12')
  })

  it('提供功能开关的读写与重置', () => {
    const sandbox = baseHost()
    runSnapshot(sandbox)
    const context = vm.createContext(sandbox)
    vm.runInContext(buildEjsTemplateSource(), context)
    const EjsTemplate = sandbox.EjsTemplate as {
      getFeatures: () => Record<string, unknown>
      setFeatures: (patch: Record<string, unknown>) => void
      resetFeatures: () => void
    }
    expect(EjsTemplate.getFeatures().generate_enabled).toBe(true)
    EjsTemplate.setFeatures({ generate_enabled: false })
    expect(EjsTemplate.getFeatures().generate_enabled).toBe(false)
    EjsTemplate.resetFeatures()
    expect(EjsTemplate.getFeatures().generate_enabled).toBe(true)
  })
})
