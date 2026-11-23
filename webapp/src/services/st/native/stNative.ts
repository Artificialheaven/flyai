/**
 * ST 原生宿主面（P0）：把卡片运行时 import 的 SillyTavern 前端模块仿真成
 * `data:` ES module，并通过 import map 映射成卡内动态 import 的路径。
 *
 * 卡片运行在 opaque-origin（`location.origin === "null"`）的 srcdoc iframe 里，
 * `import(location.origin + "/script.js")` 解析成 `"null/script.js"`。因此 import
 * map 的键是 `"null/script.js"` 这类精确字符串，而不是真实 URL。
 *
 * 模块只从 `globalThis.__FLYAI_ST_NATIVE__`（由 shim 之后、卡脚本之前注入的
 * 快照脚本生成）读数据，不引入任何网络请求，也不接触宿主 token。
 *
 * P0 只覆盖《约会大作战P-v1》运行时实际读取的导出；未列出的导出在动态 import
 * 下是 `undefined`，会以具体属性名在卡内报错，便于 P1 按卡补齐。
 */

import CHEVROTAIN_SOURCE from '../vendor/chevrotain-11.0.3.min.js?raw'

export const ST_NATIVE_SNAPSHOT_GLOBAL = '__FLYAI_ST_NATIVE__'

/**
 * 把 Chevrotain 的 ESM 导出子句改写成同模块内的命名空间对象。
 *
 * 卡内原生宏引擎要求 `lib.js` 导出真实的 `chevrotain`。若 lib 模块再静态
 * import 一个大体积 `data:` 模块，Chrome 在沙箱 srcdoc 里动态 import lib 时
 * 会一直挂起（静态 import 正常），所以把 Chevrotain 直接拼进 lib 模块，
 * 只保留一层模块解析。导出子句 `export{a as A,...}` 在这里变成
 * `const __chevrotain={A:a,...}`。
 */
const inlineChevrotain = (): string => {
  const start = CHEVROTAIN_SOURCE.lastIndexOf('export{')
  const end = start < 0 ? -1 : CHEVROTAIN_SOURCE.indexOf('}', start)
  if (start < 0 || end < 0) {
    throw new Error('chevrotain bundle: missing export clause')
  }
  const clause = CHEVROTAIN_SOURCE.slice(start + 'export{'.length, end)
  const entries = clause
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [local, exported] = entry.split(/\s+as\s+/)
      const name = (exported ?? local).trim()
      return `${name}:${local.trim()}`
    })
  return (
    CHEVROTAIN_SOURCE.slice(0, start) +
    `const __chevrotain={${entries.join(',')}};` +
    CHEVROTAIN_SOURCE.slice(end + 1)
  )
}

const DATA_URL_PREFIX = 'data:text/javascript,'

const dataModule = (source: string): string =>
  `${DATA_URL_PREFIX}${encodeURIComponent(source)}`

const MODULE_HEADER = String.raw`const __host = () => globalThis.__FLYAI_ST_NATIVE__ || {};
const __num = (value, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};
`

// ── script.js ────────────────────────────────────────────────────────────
const SCRIPT_JS_SOURCE =
  MODULE_HEADER +
  String.raw`const __state = __host();

export const main_api = 'openai';
export const name1 = String(__state.userName || '');
export const name2 = String(__state.characterName || '');
export const chat = Array.isArray(__state.chat) ? __state.chat : [];
export const chat_metadata = (__state.chatMetadata && typeof __state.chatMetadata === 'object') ? __state.chatMetadata : {};
export const characters = Array.isArray(__state.characters) ? __state.characters : [];
export const groups = [];
export const selected_group = null;
export const this_chid = 0;
export const model_list = Array.isArray(__state.modelList) ? __state.modelList : [];
export const oai_settings = (__state.oaiSettings && typeof __state.oaiSettings === 'object') ? __state.oaiSettings : {};
export const power_user = (__state.powerUser && typeof __state.powerUser === 'object') ? __state.powerUser : {};
export const extension_prompts = (__state.extensions && __state.extensions.prompts) || {};

export function getCurrentChatId() {
  return String(__host().chatId || '');
}
export function getMaxContextSize() {
  return __num(__host().maxContext, 32768);
}
export function getMaxContextTokens() {
  return __num(__host().maxContext, 32768);
}
export function getMaxPromptTokens() {
  return Math.max(1, getMaxContextSize() - getMaxResponseTokens());
}
export function getMaxResponseTokens() {
  return __num(__host().maxResponse, 4096);
}
export function getGeneratingModel() {
  const list = __host().modelList;
  const first = Array.isArray(list) ? list[0] : null;
  return String((first && (first.id || first.name)) || 'flyai');
}
export function substituteParams(value) {
  const fn = globalThis.substituteParams;
  if (typeof fn === 'function') {
    try { return fn(value); } catch (e) {}
  }
  return String(value == null ? '' : value);
}
export function saveSettingsDebounced() {}
export function parseMesExamples(value) {
  return String(value == null ? '' : value)
    .replace(/<START>[\s\S]*$/g, '')
    .trim();
}
`

// ── scripts/world-info.js ────────────────────────────────────────────────
const WORLD_INFO_SOURCE =
  MODULE_HEADER +
  String.raw`const __io = (key, fallback) => {
  const host = __host();
  const world = (host.worldInfo && typeof host.worldInfo === 'object') ? host.worldInfo : {};
  return Object.prototype.hasOwnProperty.call(world, key) ? world[key] : fallback;
};

export const DEFAULT_DEPTH = __num(__io('defaultDepth', 4), 4);
export const world_info_include_names = __io('includeNames', true) !== false;
export const world_info = {};
export const selected_world_info = [];
export const world_info_position = {
  before: 0,
  after: 1,
  ANTop: 2,
  ANBottom: 3,
  atDepth: 4,
  EMTop: 5,
  EMBottom: 6,
};
export const world_info_logic = { AND_ANY: 0, NOT_ALL: 1, NOT_ANY: 2, AND_ALL: 3 };
export const world_info_depth = __num(__io('depth', 2), 2);
export const world_info_min_activations = __num(__io('minActivations', 0), 0);
export const world_info_min_activations_depth_max = __num(__io('minActivationsDepthMax', 0), 0);
export const world_info_budget = __num(__io('budget', 25), 25);
export const world_info_recursive = __io('recursive', false) === true;
export const world_info_overflow_alert = __io('overflowAlert', false) === true;
export const world_info_case_sensitive = __io('caseSensitive', false) === true;
export const world_info_match_whole_words = __io('matchWholeWords', false) === true;
export const world_info_use_group_scoring = __io('useGroupScoring', false) === true;
export const world_info_character_strategy = __num(__io('characterStrategy', 0), 0);
export const world_info_budget_cap = __num(__io('budgetCap', 0), 0);
export const world_info_max_recursion_steps = __num(__io('maxRecursionSteps', 0), 0);
export const power_user = (__host().powerUser && typeof __host().powerUser === 'object') ? __host().powerUser : {};

function toStEntry(raw, index) {
  const source = (raw && typeof raw === 'object') ? raw : {};
  const uid = source.uid == null ? index : Number(source.uid);
  const keys = Array.isArray(source.keys) ? source.keys
    : (Array.isArray(source.key) ? source.key : []);
  const secondary = Array.isArray(source.secondary_keys) ? source.secondary_keys
    : (Array.isArray(source.keysecondary) ? source.keysecondary : []);
  return Object.assign({}, source, {
    uid: Number.isFinite(uid) ? uid : index,
    key: keys.slice(),
    keysecondary: secondary.slice(),
    comment: String(source.comment == null ? '' : source.comment),
    content: String(source.content == null ? '' : source.content),
    constant: source.constant === true,
    selective: source.selective !== false,
    disable: source.disable === true || source.enabled === false,
    position: __num(source.position, 0),
    depth: __num(source.depth, DEFAULT_DEPTH),
    order: __num(source.order, 100),
    probability: __num(source.probability, 100),
    useProbability: source.useProbability !== false,
  });
}

export function loadWorldInfo(name) {
  const helper = globalThis.TavernHelper || globalThis.tavernHelper || null;
  const book = { entries: {} };
  try {
    const data = helper && typeof helper.getWorldbook === 'function'
      ? helper.getWorldbook(name)
      : null;
    if (data && typeof data === 'object' && !Array.isArray(data) && data.entries) {
      return Promise.resolve(data);
    }
    if (Array.isArray(data)) {
      for (let i = 0; i < data.length; i += 1) {
        const entry = toStEntry(data[i], i);
        book.entries[entry.uid] = entry;
      }
    }
  } catch (e) {}
  return Promise.resolve(book);
}

export function saveWorldInfo(name, data) {
  try {
    const context = globalThis.SillyTavern && globalThis.SillyTavern.getContext
      ? globalThis.SillyTavern.getContext()
      : null;
    if (context && typeof context.saveWorldInfo === 'function') {
      return Promise.resolve(context.saveWorldInfo(name, data));
    }
  } catch (e) {}
  return Promise.resolve(data);
}
`

// ── scripts/power-user.js ────────────────────────────────────────────────
const POWER_USER_SOURCE =
  MODULE_HEADER +
  String.raw`export const power_user = (__host().powerUser && typeof __host().powerUser === 'object')
  ? __host().powerUser
  : {};
export const context_presets = [];
export const instruct_presets = [];
export const sysprompt_presets = [];
export const reasoning_presets = [];
export const persona_description_positions = {
  IN_PROMPT: 0,
  TOP_AN: 1,
  BOTTOM_AN: 2,
  AT_DEPTH: 3,
  NONE: 9,
};
`

// ── scripts/extensions.js ────────────────────────────────────────────────
const EXTENSIONS_SOURCE =
  MODULE_HEADER +
  String.raw`const __extensions = (__host().extensions && typeof __host().extensions === 'object')
  ? __host().extensions
  : {};

export const extensionNames = Array.isArray(__extensions.names) ? __extensions.names : [];
export const extension_settings = (__extensions.settings && typeof __extensions.settings === 'object')
  ? __extensions.settings
  : {};
export const extension_prompts = (__extensions.prompts && typeof __extensions.prompts === 'object')
  ? __extensions.prompts
  : {};
export const extensionTypes = {};

export function getExtensionPromptByName(name) {
  return Object.prototype.hasOwnProperty.call(extension_prompts, name)
    ? extension_prompts[name]
    : '';
}
export function getExtensionPrompt() {
  return Object.values(extension_prompts).filter((item) => typeof item === 'string').join('\n');
}
export function setExtensionPrompt(key, value) {
  extension_prompts[String(key)] = String(value == null ? '' : value);
}
export function getContext() {
  return {
    extensionSettings: extension_settings,
    extensionPrompts: extension_prompts,
  };
}
`

// ── scripts/extensions/regex/engine.js ───────────────────────────────────
const REGEX_ENGINE_SOURCE =
  MODULE_HEADER +
  String.raw`export const SCRIPT_TYPES = {
  GLOBAL: 0,
  SCOPED: 1,
  PRESET: 2,
};
export const SCRIPT_TYPE_UNKNOWN = -1;

function scriptsOf(kind) {
  const extensions = __host().extensions || {};
  const scripts = extensions.regexScripts || {};
  const list = scripts[kind];
  return Array.isArray(list) ? list : [];
}

export function getScriptsByType(scriptType, options) {
  if (scriptType === SCRIPT_TYPES.GLOBAL) return scriptsOf('global').slice();
  if (scriptType === SCRIPT_TYPES.SCOPED) return scriptsOf('scoped').slice();
  if (scriptType === SCRIPT_TYPES.PRESET) return scriptsOf('preset').slice();
  return [];
}
export function getScriptsByTypeSafe(scriptType, options) {
  return getScriptsByType(scriptType, options);
}
export function runRegexScript(script, input) {
  const raw = script && typeof script.find_regex === 'string' ? script.find_regex : '';
  if (!raw) return String(input == null ? '' : input);
  try {
    return String(input == null ? '' : input).replace(new RegExp(raw, 'g'), String(script.replace_string || ''));
  } catch (e) {
    return String(input == null ? '' : input);
  }
}
`

// ── scripts/tokenizers.js ────────────────────────────────────────────────
const TOKENIZERS_SOURCE =
  MODULE_HEADER +
  String.raw`export const BYTES_PER_TOKEN = 3.35;
export const CHARACTERS_PER_TOKEN_RATIO = BYTES_PER_TOKEN;
export const tokenizers = {};
export const ENCODE_TOKENIZERS = [];
export const TEXTGEN_TOKENIZERS = [];

function approximate(value) {
  const text = String(value == null ? '' : value);
  // 沙箱内没有真实 tokenizer；按 UTF-8 字节数估算，只用于长度预算。
  let bytes = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff) { bytes += 2; i += 1; }
    else bytes += 3;
  }
  return Math.max(0, Math.ceil(bytes / BYTES_PER_TOKEN));
}

export function guesstimate(str) {
  return approximate(str);
}
export function getTokenCount(str, padding) {
  return approximate(str) + __num(padding, 0);
}
export async function getTokenCountAsync(str, padding) {
  return approximate(str) + __num(padding, 0);
}
export function getTokenizerModel() {
  const tokenizersState = __host().tokenizers || {};
  return String(tokenizersState.model || 'gpt-3.5-turbo');
}
export function getAvailableTokenizers() {
  return [];
}
export function getTokenizerBestMatch() {
  return getTokenizerModel();
}
export async function countTokensOpenAIAsync(messages) {
  const list = Array.isArray(messages) ? messages : [];
  let total = 0;
  for (const item of list) total += approximate(item && item.content);
  return total;
}
`

// ── scripts/tags.js ──────────────────────────────────────────────────────
const TAGS_SOURCE =
  MODULE_HEADER +
  String.raw`export const tag_filter_type = { AND: 0, OR: 1, NOT: 2 };
export const tag_import_setting = { AS_NONE: 0, AS_TAG: 1 };
export const tag_sort_mode = { MANUAL: 0, ALPHABETICAL: 1, BY_ENTRY_COUNT: 2 };

export function getTagKeyForEntity(entityOrKey) {
  if (typeof entityOrKey === 'string' && entityOrKey) return entityOrKey;
  return null;
}
export function getTagKeyForEntityElement() {
  return null;
}
export function getTagMap() {
  const tags = __host().tags || {};
  return (tags.tagMap && typeof tags.tagMap === 'object') ? tags.tagMap : {};
}
export function getTagsList() {
  return [];
}
export function searchCharByName() {
  return null;
}
`

// ── lib.js ───────────────────────────────────────────────────────────────
const LIB_SOURCE =
  inlineChevrotain() +
  '\n' +
  MODULE_HEADER +
  String.raw`function minimalLodash() {
  const isObject = (v) => v != null && typeof v === 'object';
  const clone = (v) => {
    if (v == null || typeof v !== 'object') return v;
    try { return JSON.parse(JSON.stringify(v)); } catch (e) { return v; }
  };
  const get = (obj, path, fallback) => {
    const keys = Array.isArray(path) ? path : String(path == null ? '' : path).split('.');
    let cur = obj;
    for (const key of keys) {
      if (cur == null) return fallback;
      cur = cur[key];
    }
    return cur === undefined ? fallback : cur;
  };
  const lodash = {
    cloneDeep: clone,
    get,
    has: (obj, path) => get(obj, path, undefined) !== undefined,
    isEmpty: (v) => v == null
      || (Array.isArray(v) ? v.length === 0 : (typeof v === 'string' ? v.length === 0 : Object.keys(v).length === 0)),
    isArray: Array.isArray,
    isObject,
    isPlainObject: (v) => isObject(v) && Object.getPrototypeOf(v) === Object.prototype,
    isString: (v) => typeof v === 'string',
    isNumber: (v) => typeof v === 'number',
    isBoolean: (v) => typeof v === 'boolean',
    isFunction: (v) => typeof v === 'function',
    isNil: (v) => v == null,
    map: (v, fn) => (Array.isArray(v) ? v.map(fn) : []),
    filter: (v, fn) => (Array.isArray(v) ? v.filter(fn) : []),
    find: (v, fn) => (Array.isArray(v) ? v.find(fn) : undefined),
    some: (v, fn) => (Array.isArray(v) ? v.some(fn) : false),
    every: (v, fn) => (Array.isArray(v) ? v.every(fn) : true),
    forEach: (v, fn) => { (Array.isArray(v) ? v : []).forEach(fn); },
    uniq: (v) => Array.from(new Set(Array.isArray(v) ? v : [])),
    flatten: (v) => [].concat.apply([], Array.isArray(v) ? v : []),
    includes: (v, item) => Array.isArray(v) ? v.indexOf(item) >= 0 : String(v || '').indexOf(item) >= 0,
    pick: (obj, keys) => {
      const out = {};
      for (const key of (Array.isArray(keys) ? keys : [])) {
        if (obj && key in obj) out[key] = obj[key];
      }
      return out;
    },
    omit: (obj, keys) => {
      const blocked = Array.isArray(keys) ? keys : [];
      const out = {};
      if (!obj) return out;
      for (const key of Object.keys(obj)) {
        if (blocked.indexOf(key) < 0) out[key] = obj[key];
      }
      return out;
    },
    merge: (target, source) => {
      if (!isObject(source)) return target;
      for (const key of Object.keys(source)) {
        const value = source[key];
        if (isObject(value) && !Array.isArray(value) && isObject(target[key])) {
          lodash.merge(target[key], value);
        } else {
          target[key] = clone(value);
        }
      }
      return target;
    },
    escape: (v) => String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
    template: (text) => () => String(text == null ? '' : text),
    isEqual: (a, b) => JSON.stringify(a) === JSON.stringify(b),
  };
  return lodash;
}

function parseYaml(text) {
  const raw = String(text == null ? '' : text);
  try { return JSON.parse(raw); } catch (e) {}
  const lines = raw.split(/\r?\n/);
  let index = 0;
  const parseScalar = (value) => {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    if (trimmed === 'null' || trimmed === '~') return null;
    if (trimmed === 'true') return true;
    if (trimmed === 'false') return false;
    if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return Number(trimmed);
    if ((trimmed[0] === '"' && trimmed[trimmed.length - 1] === '"')
      || (trimmed[0] === "'" && trimmed[trimmed.length - 1] === "'")) {
      return trimmed.slice(1, -1);
    }
    if (trimmed[0] === '[' || trimmed[0] === '{') {
      try { return JSON.parse(trimmed); } catch (e) {}
    }
    return trimmed;
  };
  const nextMeaningful = () => {
    while (index < lines.length) {
      const line = lines[index];
      if (line.trim() === '' || /^\s*#/.test(line)) { index += 1; continue; }
      return line;
    }
    return null;
  };
  const parseBlock = (indent) => {
    const first = nextMeaningful();
    if (first == null) return {};
    const isList = /^\s*-\s+/.test(first);
    const container = isList ? [] : {};
    while (index < lines.length) {
      const line = lines[index];
      if (line.trim() === '' || /^\s*#/.test(line)) { index += 1; continue; }
      const lineIndent = line.length - line.trimStart().length;
      if (lineIndent < indent) break;
      if (lineIndent > indent) break;
      if (isList) {
        const itemMatch = /^\s*-\s+(.*)$/.exec(line);
        if (!itemMatch) break;
        index += 1;
        const rest = itemMatch[1];
        const nested = /^(.*):\s*$/.exec(rest);
        if (nested) {
          const obj = {};
          obj[nested[1].trim()] = parseBlock(indent + 2);
          container.push(obj);
        } else {
          container.push(parseScalar(rest));
        }
        continue;
      }
      const pair = /^\s*([^:#]+):\s*(.*)$/.exec(line);
      if (!pair) { index += 1; continue; }
      const key = pair[1].trim();
      const value = pair[2];
      index += 1;
      if (value.trim() === '') {
        const following = nextMeaningful();
        if (following != null) {
          const nextIndent = following.length - following.trimStart().length;
          if (nextIndent > indent) {
            container[key] = parseBlock(nextIndent);
            continue;
          }
        }
        container[key] = null;
      } else {
        container[key] = parseScalar(value);
      }
    }
    return container;
  };
  return parseBlock(nextMeaningful() ? (nextMeaningful().length - nextMeaningful().trimStart().length) : 0);
}

const lodash = globalThis._ || minimalLodash();
const yaml = globalThis.jsyaml || globalThis.YAML || {
  parse: parseYaml,
  stringify: (value, replacer, space) => JSON.stringify(value, replacer, space),
  load: parseYaml,
  dump: (value) => JSON.stringify(value, null, 2),
};
const DOMPurify = globalThis.DOMPurify || {
  sanitize: (value) => String(value == null ? '' : value),
  addHook: () => {},
  setConfig: () => {},
  removeAllHooks: () => {},
};
const Handlebars = globalThis.Handlebars || {
  compile: () => () => '',
  registerHelper: () => {},
};

export {
  lodash,
  yaml,
  DOMPurify,
  Handlebars,
};
export const moment = globalThis.moment || null;
export const showdown = globalThis.showdown || null;
export const localforage = globalThis.localforage || null;
export const Fuse = globalThis.Fuse || null;
export const hljs = globalThis.hljs || null;
// 卡内原生宏引擎要求真实 Chevrotain（Lexer/CstParser/createToken）。
export const chevrotain = __chevrotain;
export const sha256 = null;
export const gzipSync = null;
export const gzip = null;
`

// ── scripts/authors-note.js ──────────────────────────────────────────────
const AUTHORS_NOTE_SOURCE =
  MODULE_HEADER +
  String.raw`export const NOTE_MODULE_NAME = 'authors_note';
export const shouldWIAddPrompt = false;
export const metadata_keys = {
  prompt: 'prompt',
  depth: 'depth',
  role: 'role',
  interval: 'interval',
  position: 'position',
  scan: 'scan',
};
export const extension_prompt_types = {
  IN_PROMPT: 0,
  IN_CHAT: 1,
  BEFORE_PROMPT: 2,
  NONE: 9,
};
export const extension_prompt_roles = { SYSTEM: 0, USER: 1, ASSISTANT: 2 };
export function setFloatingPrompt() {}
export function initAuthorsNote() {}
`

// ── scripts/openai.js ────────────────────────────────────────────────────
const OPENAI_SOURCE =
  MODULE_HEADER +
  String.raw`const __hostState = __host();
const __oai = (__hostState.oaiSettings && typeof __hostState.oaiSettings === 'object')
  ? __hostState.oaiSettings
  : {};
const __power = (__hostState.powerUser && typeof __hostState.powerUser === 'object')
  ? __hostState.powerUser
  : {};

export const oai_settings = __oai;
export const model_list = Array.isArray(__hostState.modelList) ? __hostState.modelList : [];
export const power_user = __power;
export const chat_completion_sources = { CUSTOM: 'custom', OPENAI: 'openai' };
export const openai_messages_token = {};
export const openai_max_context = __num(__hostState.maxContext, 32768);
export const openai_max_tokens = __num(__hostState.maxResponse, 4096);
export const custom_prompt_post_processing_types = { NONE: 0, MERGE: 1, SEMI: 2, STRICT: 3 };

export function getChatCompletionModel() {
  return String(__oai.openai_model || 'flyai');
}
export function getChatCompletionSource() {
  return String(__oai.chat_completion_source || 'custom');
}
export function isServiceAllowed() {
  return true;
}
export function openAICompatibleModels() {
  return model_list;
}
`

const NATIVE_MODULES: ReadonlyArray<readonly [string, string]> = [
  ['script.js', SCRIPT_JS_SOURCE],
  ['scripts/world-info.js', WORLD_INFO_SOURCE],
  ['scripts/power-user.js', POWER_USER_SOURCE],
  ['scripts/extensions.js', EXTENSIONS_SOURCE],
  ['scripts/extensions/regex/engine.js', REGEX_ENGINE_SOURCE],
  ['scripts/tokenizers.js', TOKENIZERS_SOURCE],
  ['scripts/tags.js', TAGS_SOURCE],
  ['lib.js', LIB_SOURCE],
  ['scripts/authors-note.js', AUTHORS_NOTE_SOURCE],
  ['scripts/openai.js', OPENAI_SOURCE],
]

/** 已仿真的 ST 模块路径（相对站点根）。 */
export const ST_NATIVE_MODULE_PATHS = NATIVE_MODULES.map(([path]) => path)

/**
 * 构造 import map 的 imports 表。默认覆盖 `"null/<path>"`；在父页面里构建时
 * 额外覆盖真实 origin，兼容卡内 `<base href>` 把相对 import 解析成父页面 URL 的情况。
 */
export const buildNativeImportMapEntries = (): Record<string, string> => {
  const entries: Record<string, string> = {}
  const origins = ['null']
  try {
    if (typeof location !== 'undefined' && location.origin && location.origin !== 'null') {
      origins.push(location.origin)
    }
  } catch {
    // 无 location（测试环境）时只用 null
  }
  for (const path of ST_NATIVE_MODULE_PATHS) {
    const source = NATIVE_MODULES.find(([item]) => item === path)?.[1] ?? ''
    const url = dataModule(source)
    for (const origin of origins) {
      entries[`${origin}/${path}`] = url
    }
  }
  return entries
}

/** 读取某个模块的 data: 源码；测试与诊断使用。 */
export const buildNativeModuleUrl = (path: string): string => {
  const found = NATIVE_MODULES.find(([item]) => item === path)
  return found ? dataModule(found[1]) : ''
}

/**
 * 快照 + EJS 之前的原生宿主脚本：把 shim 的会话状态整理成 ST 形状，供 data:
 * 模块读取。必须在 shim 之后、卡内 module 脚本之前执行。
 */
export const ST_NATIVE_SNAPSHOT_SOURCE = String.raw`(function(){
  var g = globalThis;
  function isFn(value){ return typeof value === 'function'; }
  function isObject(value){ return value != null && typeof value === 'object'; }
  var ctx = {};
  try {
    if (g.SillyTavern && isFn(g.SillyTavern.getContext)) {
      ctx = g.SillyTavern.getContext() || {};
    }
  } catch (e) { ctx = {}; }
  var helper = g.TavernHelper || g.tavernHelper || null;
  function helperCall(name, fallback){
    try {
      if (helper && isFn(helper[name])) return helper[name]();
    } catch (e) {}
    return fallback;
  }
  var characters = Array.isArray(ctx.characters) ? ctx.characters : [];
  var character = characters[0] || {};
  var worldNames = helperCall('getWorldbookNames', []);
  if (!Array.isArray(worldNames)) worldNames = [];
  var charWorld = helperCall('getCharWorldbookNames', null);
  var promptMap = isObject(ctx.extensionPrompts) ? ctx.extensionPrompts : {};
  var tagMap = isObject(ctx.tagMap) ? ctx.tagMap : {};
  var snapshot = {
    version: 1,
    chatId: String(ctx.chatId || ''),
    characterId: Number(ctx.characterId || 0) || 0,
    characterName: String(ctx.name2 || character.name || helperCall('getCurrentCharName', '') || ''),
    userName: String(ctx.name1 || helperCall('getCurrentPersonaName', '') || 'User'),
    groupId: ctx.groupId == null ? null : ctx.groupId,
    chat: Array.isArray(ctx.chat) ? ctx.chat : [],
    chatMetadata: isObject(ctx.chatMetadata) ? ctx.chatMetadata : {},
    characters: characters,
    mainApi: 'openai',
    maxContext: 32768,
    maxResponse: 4096,
    modelList: [
      { id: 'flyai', name: 'FlyAI', object: 'model', owned_by: 'flyai' }
    ],
    oaiSettings: {
      chat_completion_source: 'custom',
      openai_model: 'flyai',
      custom_url: '',
      streaming: false,
      temperature: 1,
      top_p: 1,
      openai_max_context: 32768,
      openai_max_tokens: 4096,
      reverse_proxy: '',
      proxy_password: '',
      preset_settings: '',
      stream_openai: false,
      request_images: false,
    },
    powerUser: {
      experimental_macro_engine: false,
      instruct: { enabled: false, input_sequence: '', output_sequence: '', wrap: true },
      context: { enabled: false, preset: '' },
      sysprompt: { enabled: false, name: '', content: '' },
      custom_stopping_strings: '',
      custom_stopping_strings_macro: true,
      request_token_probabilities: false,
      persona_description: '',
      persona_description_position: 0,
      persona_description_lorebook: '',
      strip_examples: true,
      mes_example: '',
      Wi_format: '{0}',
      scenario_format: '{{scenario}}',
      personality_format: '{{personality}}',
      group_nudge_prompt: '',
      new_chat_prompt: '',
      new_group_chat_prompt: '',
      new_example_chat_prompt: '',
      continue_nudge_prompt: '',
      impersonation_prompt: '',
      quiet_prompt: '',
      auto_connect: false,
      bypass_availability: false,
      show_avatar: false,
      default_persona: '',
      send_if_empty: '',
      use_default_banner: false,
    },
    worldInfo: {
      names: worldNames,
      charWorldName: charWorld && charWorld.primary ? String(charWorld.primary) : '',
      defaultDepth: 4,
      includeNames: true,
      depth: 2,
      minActivations: 0,
      minActivationsDepthMax: 0,
      budget: 25,
      recursive: false,
      overflowAlert: false,
      caseSensitive: false,
      matchWholeWords: false,
      useGroupScoring: false,
      characterStrategy: 0,
      budgetCap: 0,
      maxRecursionSteps: 0,
    },
    extensions: {
      names: ['third-party/JS-Slash-Runner', 'third-party/ST-EJS'],
      settings: {
        variables: { global: {} },
        disabledExtensions: [],
        note: { allowWIScan: false },
        vectors: { enabled_world_info: false, enabled_for_all: false },
        regex: [],
        character_allowed_regex: [],
        preset_allowed_regex: {},
      },
      prompts: promptMap,
      regexScripts: { global: [], scoped: [], preset: [] },
    },
    tokenizers: { model: 'gpt-3.5-turbo' },
    tags: { tagMap: tagMap },
  };
  g.__FLYAI_ST_NATIVE__ = snapshot;
  g.__FLYAI_NATIVE_HOST__ = true;
  // 卡内代码常直接读 SillyTavern.getContext()；补齐快照里的字段，但不覆盖
  // shim 已有的 getContext/写入 API。
  if (g.SillyTavern && isFn(g.SillyTavern.getContext)) {
    var originalGetContext = g.SillyTavern.getContext;
    g.SillyTavern.getContext = function(){
      var base = originalGetContext.apply(this, arguments) || {};
      if (base.mainApi == null) base.mainApi = snapshot.mainApi;
      if (!isObject(base.tagMap)) base.tagMap = snapshot.tags.tagMap;
      if (!isObject(base.extensionPrompts)) base.extensionPrompts = promptMap;
      if (base.groupId == null) base.groupId = snapshot.groupId;
      return base;
    };
  }
  if (helper && isFn(helper.getCurrentCharacterId) && !isFn(helper.getCurrentCharacterName)) {
    helper.getCurrentCharacterName = function(){ return snapshot.characterName; };
  }
})();`
