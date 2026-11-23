/**
 * 沙箱内的 `window.EjsTemplate`：把 `ejs.ts` 的自包含运行时包装成酒馆助手
 * （ST-Prompt-Template）对外暴露的接口。
 *
 * 卡内运行时实际使用：
 *
 * ```js
 * const compiled = await EjsTemplate.compileTemplate(template, { options }, locals)
 * return await compiled.call(locals, locals)
 * ```
 *
 * 因此 `compileTemplate` 返回可调用的函数，并把 locals 合并进模板上下文。
 * 变量写入只留在模板执行期，不回写会话。
 */

import { EJS_RUNTIME_SOURCE } from '../ejs.js'

const FEATURES_DEFAULTS = `{
    enabled: true,
    generate_enabled: true,
    generate_loader_enabled: false,
    inject_loader_enabled: false,
    render_enabled: true,
    render_loader_enabled: false,
    code_blocks_enabled: true,
    raw_message_evaluation_enabled: false,
    filter_message_enabled: true,
    depth_limit: -1,
    autosave_enabled: false,
    preload_worldinfo_enabled: false,
    with_context_disabled: false,
    debug_enabled: false,
    invert_enabled: false,
    compile_workers: false,
    sandbox: false,
    cache_enabled: 0,
    cache_size: 64,
    cache_hasher: 'h64ToString'
  }`

/**
 * 生成 `window.EjsTemplate` 的脚本源码（不含 `<script>` 标签）。
 * 调用方负责在 shim 与原生快照之后、卡内 module 脚本之前注入。
 */
export const buildEjsTemplateSource = (): string =>
  '(function(){\n' +
  '  var runtime = ' +
  EJS_RUNTIME_SOURCE +
  ';\n' +
  String.raw`  var DEFAULTS = ` +
  FEATURES_DEFAULTS +
  String.raw`;
  var features = {};
  for (var defaultKey in DEFAULTS) features[defaultKey] = DEFAULTS[defaultKey];
  function clone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch (e) { return {}; }
  }
  function helper(){
    return globalThis.TavernHelper || globalThis.tavernHelper || null;
  }
  function allVariables(){
    var api = helper();
    try {
      if (api && typeof api.getAllVariables === 'function') {
        var value = api.getAllVariables();
        if (value && typeof value === 'object') return value;
      }
    } catch (e) {}
    return {};
  }
  async function prepareContext(extra, lastMessageId){
    var host = globalThis.__FLYAI_ST_NATIVE__ || {};
    var variables = allVariables();
    var base = {};
    for (var key in variables) base[key] = variables[key];
    base.variables = variables;
    base.char = host.characterName || '';
    base.user = host.userName || '';
    base.chatId = host.chatId || '';
    base.characterId = host.characterId == null ? 0 : host.characterId;
    base.lastMessageId = typeof lastMessageId === 'number'
      ? lastMessageId
      : (Array.isArray(host.chat) ? host.chat.length - 1 : 0);
    if (extra && typeof extra === 'object') {
      for (var extraKey in extra) base[extraKey] = extra[extraKey];
    }
    return base;
  }
  async function compileTemplate(code, options, locals){
    var prepared = await prepareContext(locals);
    var template = String(code == null ? '' : code);
    function compiled(context){
      var merged = {};
      for (var key in prepared) merged[key] = prepared[key];
      if (context && typeof context === 'object') {
        for (var contextKey in context) merged[contextKey] = context[contextKey];
      }
      return runtime.render(template, merged);
    }
    return compiled;
  }
  async function evalTemplate(code, context, options){
    var compiled = await compileTemplate(code, options, context);
    return compiled(context);
  }
  function getFeatures(){
    return clone(features);
  }
  function setFeatures(patch){
    if (patch && typeof patch === 'object') {
      for (var key in patch) {
        if (Object.prototype.hasOwnProperty.call(features, key)) features[key] = patch[key];
      }
    }
  }
  function resetFeatures(){
    features = {};
    for (var key in DEFAULTS) features[key] = DEFAULTS[key];
  }
  function getSyntaxErrorInfo(){
    // P0 没有独立的编译检查接口；语法错误会在 click 时由 compileTemplate 抛出。
    return Promise.resolve('');
  }
  window.EjsTemplate = {
    compileTemplate: compileTemplate,
    evalTemplate: evalTemplate,
    evaltemplate: evalTemplate,
    prepareContext: prepareContext,
    allVariables: function(){ return allVariables(); },
    getSyntaxErrorInfo: getSyntaxErrorInfo,
    getFeatures: getFeatures,
    setFeatures: setFeatures,
    resetFeatures: resetFeatures
  };
})();`
