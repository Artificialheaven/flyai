/**
 * 扩展宿主模块注册表：路径与符号必须与后端 `flyai/extensions/scanner.py`
 * 的 HOST_MODULES 保持一致（安装扫描据此给出缺口报告）。
 *
 * 所有模块都是独立 data: ESM，只通过 `window.__FLYAI_EXT_HOST__` 与宿主通信，
 * 不互相 import，避免 data: 模块之间的相对解析问题。
 */

export type ExtensionModuleDef = {
  path: string
  source: string
  exports: string[]
}

const scriptJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
const S = H.state || {};
export const eventSource = H.events;
export const event_types = H.event_types;
export const extension_settings = H.settings;
export const extension_prompts = H.extension_prompts;
export const characters = S.characters || [];
export const chat = S.chat || [];
export const chat_metadata = S.chat_metadata || {};
export const name2 = S.name2 || '';
export const this_chid = '0';
export const systemUserName = 'System';
export const user_avatar = S.user_avatar || 'user-default.png';
export const online_status = 'no_connection';
export const is_send_press = false;
export const main_api = 'openai';
export const CONNECT_API_MAP = {};
export const animation_duration = 0;
export const animation_easing = 'ease';
export const system_message_types = { HELP:'help', WELCOME:'welcome', EMPTY:'empty', GENERIC:'generic', COMMENT:'comment', SLASH_COMMAND:'slash_command', FORMATTING:'formatting', HOT_SWAP:'hot_swap', NARRATOR:'narrator' };
export const streamingProcessor = null;
export const extension_prompt_types = { IN_PROMPT: 0, IN_CHAT: 1, BEFORE_PROMPT: 2 };
export const extension_prompt_roles = { SYSTEM: 0, USER: 1, ASSISTANT: 2 };
export const getCurrentChatId = () => S.chat_id || null;
export const getMaxPromptTokens = () => Number(S.max_prompt_tokens || 8192);
export const isStreamingEnabled = () => true;
export const saveSettingsDebounced = () => H.saveSettingsDebounced();
export const substituteParams = (text) => H.substituteParams(text);
export const substituteParamsExtended = (text) => H.substituteParams(text);
export const messageFormatting = (text) => H.messageFormatting(text);
export const setExtensionPrompt = (key, value, position, depth, scan, role) =>
  H.request('prompt.setExtension', { key, value, position, depth, scan, role });
export const getRequestHeaders = () => ({ 'Content-Type': 'application/json' });
export const generateQuietPrompt = (args) => H.generateQuietPrompt(args);
export const generateRaw = (args) => H.generateRaw(args);
export const saveChatConditional = () => H.request('chat.save', {});
export const reloadCurrentChat = () => H.request('chat.reload', {});
export const getUserAvatar = () => H.getUserAvatar();
export const getCharacterAvatar = () => H.getCharacterAvatar();
export const formatCharacterAvatar = (avatar) => H.formatCharacterAvatar(avatar);
export const getContext = () => S.context || {};
export { eventSource as default };
`

const utilsJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
export const collapseSpaces = (text) => String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
export const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const escapeHtml = (text) => String(text == null ? '' : text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
export const escapeRegex = (text) => String(text == null ? '' : text).replace(/[.*+?^$()|[\]{}\\]/g, '\\$&');
export const onlyUnique = (value, index, self) => self.indexOf(value) === index;
export const stringFormat = (text, ...args) => String(text == null ? '' : text).replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)] ?? ''));
export const uuidv4 = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
  const r = (Math.random() * 16) | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
});
export const waitUntilCondition = async (condition, timeout = 10000, interval = 100) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await condition()) return true;
    await delay(interval);
  }
  return false;
};
export const isValidUrl = (value) => { try { new URL(String(value)); return true; } catch { return false; } };
export const getCharaFilename = (avatar) => String(avatar || '').replace(/\.[^.]+$/, '');
export const getFileExtension = (name) => { const m = String(name || '').match(/\.([^.\/]+)$/); return m ? m[1].toLowerCase() : ''; };
export const sanitizeFilename = (name) => String(name || '').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').slice(0, 200);
export const getSanitizedFilename = (name) => Promise.resolve(sanitizeFilename(name));
export const getSortableDelay = () => (H.state.is_mobile ? 750 : 50);
export const getStringHash = (value) => {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  const str = String(value == null ? '' : value);
  for (let i = 0; i < str.length; i += 1) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761); h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return 4294967296 * (2097151 & h2) + (h1 >>> 0);
};
export const deepMerge = (target, source) => {
  const out = Array.isArray(target) ? target.slice() : Object.assign({}, target);
  if (!source || typeof source !== 'object') return out;
  for (const key of Object.keys(source)) {
    const value = source[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      out[key] = deepMerge(out[key] && typeof out[key] === 'object' ? out[key] : {}, value);
    } else if (Array.isArray(value)) out[key] = value.slice();
    else out[key] = value;
  }
  return out;
};
export const debounce = (fn, ms) => {
  let timer = 0;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
};
export const debounceAsync = (fn, ms) => {
  let timer = 0;
  return (...args) => new Promise((resolve) => {
    clearTimeout(timer); timer = setTimeout(() => resolve(fn(...args)), ms);
  });
};
export const equalsIgnoreCaseAndAccents = (a, b) => String(a ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase() === String(b ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
export const includesIgnoreCaseAndAccents = (a, b) => String(a ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().includes(String(b ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase());
export const extractAllWords = (text) => String(text == null ? '' : text).split(/\s+/).filter(Boolean);
export const getUniqueName = (name, existing) => {
  const base = String(name || 'item'); const used = new Set(existing || []); let i = 1; let out = base;
  while (used.has(out)) { out = base + ' ' + (++i); } return out;
};
export const isFalseBoolean = (value) => String(value).toLowerCase() === 'false';
export const isTrueBoolean = (value) => String(value).toLowerCase() === 'true';
export const regexFromString = (value) => {
  const match = String(value ?? '').match(/^\/(.*)\/([a-z]*)$/);
  if (match) return new RegExp(match[1], match[2]);
  return new RegExp(String(value ?? ''));
};
export const splitRecursive = (text, limit, separator = ' ') => {
  const words = String(text ?? '').split(separator); const out = []; let cur = '';
  for (const word of words) { if (cur && (cur + separator + word).length > limit) { out.push(cur); cur = word; } else cur = cur ? cur + separator + word : word; }
  if (cur) out.push(cur); return out;
};
export const trimToEndSentence = (text) => { const value = String(text ?? ''); const m = value.match(/^[\s\S]*[.!?。！？]/); return m ? m[0] : value; };
export const trimToStartSentence = (text) => { const value = String(text ?? ''); const i = value.search(/[.!?。！？]/); return i >= 0 ? value.slice(i + 1) : value; };
export const humanizedDateTime = (date) => new Date(date || Date.now()).toLocaleString();
export const isMobile = () => !!H.state.is_mobile;
export const getBase64Async = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob); });
export const getFileText = (file) => file.text();
export const download = (data, name, type) => {
  const blob = data instanceof Blob ? data : new Blob([data], { type: type || 'text/plain' });
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = name || 'download'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
export const saveBase64AsFile = () => { throw new Error('ST host interface not implemented: utils.saveBase64AsFile'); };
export const ensureImageFormatSupported = (file) => file;
export const findChar = () => undefined;
export const flashHighlight = () => {};
export const initScrollHeight = () => {};
export const resetScrollHeight = () => {};
export const loadFileToDocument = () => {};
export const setInfoBlock = () => {};
export const showFontAwesomePicker = () => {};
export const getVideoThumbnail = () => Promise.reject(new Error('not implemented'));
`

const extensionsJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const extension_settings = H.settings;
export const extensionNames = H.state.extension_names || [];
export const EMPTY_AUTHOR = { name: '', url: '' };
export const modules = [];
export const getContext = () => H.state.context || {};
export const saveMetadataDebounced = () => H.request('chat.saveMetadata', {});
export const renderExtensionTemplateAsync = (name, template, args) =>
  H.request('template.render', { name, template, args: args || {} });
export const writeExtensionField = (characterId, key, value) =>
  H.request('character.writeField', { characterId, key, value });
export const getApiUrl = () => '';
export const doExtrasFetch = () => Promise.reject(new Error('Extras is not supported'));
export const installExtension = () => Promise.reject(new Error('Extension install must be done in FlyAI settings'));
export const deleteExtension = () => Promise.reject(new Error('Extension delete must be done in FlyAI settings'));
export const getAuthorFromUrl = () => '';
export const isOfficialExtension = () => false;
export const openThirdPartyExtensionMenu = () => {};
export function ModuleWorkerWrapper(){ this.worker = null; this.init = () => {}; this.destroy = () => {}; }
export default extension_settings;
`

const libJs = String.raw`
export const Fuse = class { constructor(){ this.list = []; } search(){ return []; } setCollection(list){ this.list = list; } };
export const hljs = { highlightAuto: (text) => ({ value: String(text ?? '') }), initHighlightingOnLoad: () => {} };
export const Popper = { createPopper: () => ({ destroy: () => {}, update: () => {} }) };
export const lodash = {
  get: (obj, path, def) => { const parts = Array.isArray(path) ? path : String(path ?? '').split('.'); let cur = obj; for (const key of parts) { if (cur == null) return def; cur = cur[key]; } return cur === undefined ? def : cur; },
  set: (obj, path, value) => { const parts = Array.isArray(path) ? path : String(path ?? '').split('.'); let cur = obj; for (let i=0;i<parts.length-1;i++){ if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {}; cur = cur[parts[i]]; } cur[parts[parts.length-1]] = value; return obj; },
  clone: (value) => (value == null ? value : JSON.parse(JSON.stringify(value))),
  merge: Object.assign,
  isEmpty: (value) => value == null || (typeof value === 'object' ? Object.keys(value).length === 0 : String(value).length === 0),
  isObject: (value) => value != null && typeof value === 'object' && !Array.isArray(value),
  isArray: Array.isArray,
};
export const lodashFn = lodash;
export const DOMPurify = { sanitize: (html) => String(html ?? '').replace(/<script[\s\S]*?<\/script>/gi, '') };
export const morphdom = (from, to) => { if (from && to && from.replaceWith) from.replaceWith(to); return from; };
export { lodash as _ };
`

const popupJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const POPUP_TYPE = H.POPUP_TYPE;
export const POPUP_RESULT = H.POPUP_RESULT;
export const Popup = H.Popup;
export const callGenericPopup = H.callGenericPopup;
`

const constantsJs = String.raw`
export const IMAGE_OVERSWIPE = false;
export const MEDIA_DISPLAY = { INLINE: 'inline', GALLERY: 'gallery' };
export const MEDIA_REQUEST_TYPE = { IMAGE: 1, VIDEO: 2, AUDIO: 4 };
export const MEDIA_SOURCE = { MESSAGE: 'message', ATTACHMENT: 'attachment', MODEL: 'model' };
export const MEDIA_TYPE = { IMAGE: 'image', VIDEO: 'video', AUDIO: 'audio' };
export const SCROLL_BEHAVIOR = { NONE: 'none', KEEP: 'keep', RESET: 'reset' };
export const SWIPE_DIRECTION = { LEFT: 'left', RIGHT: 'right', UP: 'up', DOWN: 'down' };
export const VIDEO_EXTENSIONS = ['mp4', 'webm', 'mov', 'avi', 'mkv'];
export const debounce_timeout = { short: 100, normal: 300, long: 1000 };
`

const i18nJs = String.raw`
export const translate = (key) => String(key ?? '');
export const t = (strings, ...args) => {
  if (Array.isArray(strings)) {
    return strings.reduce((out, part, index) => out + part + (index < args.length ? String(args[index]) : ''), '');
  }
  return String(strings ?? '');
};
`

const rossJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const dragElement = () => {};
export const getMessageTimeStamp = () => new Date().toISOString();
export const humanizedDateTime = (date) => new Date(date || Date.now()).toLocaleString();
export const isMobile = () => !!H.state.is_mobile;
`

const secretsJs = String.raw`
export const SECRET_KEYS = { OPENAI: 'api_key_openai', ANTHROPIC: 'api_key_anthropic', MAKERSUITE: 'api_key_makersuite', VERTEXAI: 'api_key_vertexai', OPENROUTER: 'api_key_openrouter' };
export const secret_state = {};
export const getSecretLabelById = (id) => String(id || '');
`

const powerUserJs = String.raw`
export const power_user = {};
export const collapseNewlines = (text) => String(text ?? '').replace(/\n{3,}/g, '\n\n');
export const loadMovingUIState = () => {};
export const performFuzzySearch = () => [];
export const registerDebugFunction = () => {};
`

const groupChatsJs = String.raw`
export const groups = [];
export const hideMutedSprites = false;
export const is_group_generating = false;
export const selected_group = null;
`

const openaiJs = String.raw`
export const POLLINATIONS_ENDPOINT = 'https://image.pollinations.ai/prompt/';
export const oai_settings = {};
`

const chatsJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const deleteAttachment = () => Promise.reject(new Error('Attachments are not supported'));
export const deleteMediaFromServer = () => Promise.reject(new Error('Media deletion is not supported'));
export const getDataBankAttachments = () => [];
export const getDataBankAttachmentsForSource = () => [];
export const getFileAttachment = () => Promise.reject(new Error('Attachments are not supported'));
export const uploadFileAttachmentToServer = () => Promise.reject(new Error('Attachments are not supported'));
`

const tokenizersJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const tokenizers = {};
export const getFriendlyTokenizerName = () => 'approximate';
export const getTextTokens = (text) => Array.from(String(text ?? '')).map((ch) => ch.codePointAt(0));
export const getTokenCountAsync = async (text) => Math.ceil(String(text ?? '').length / 2);
`

const presetManagerJs = String.raw`
export const getPresetManager = () => null;
`

const worldInfoJs = String.raw`
export const getSortedEntries = (entries) => {
  const list = Array.isArray(entries) ? entries.slice() : (entries && typeof entries === 'object' ? Object.values(entries) : []);
  return list.sort((a, b) => Number(a && a.order || 100) - Number(b && b.order || 100));
};
`

const accountStorageJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const accountStorage = {
  getItem: (key) => H.request('storage.get', { key }),
  setItem: (key, value) => H.request('storage.set', { key, value }),
  removeItem: (key) => H.request('storage.remove', { key }),
};
`

const reasoningJs = String.raw`
export const removeReasoningFromString = (text) => String(text ?? '');
export const updateReasoningUI = () => {};
`

const textgenJs = String.raw`
export const isJsonSchemaSupported = () => false;
export const textgen_types = { OOBA: 'ooba', MANCER: 'mancer', APHRODITE: 'aphrodite', TABBY: 'tabby' };
export const textgenerationwebui_settings = {};
`

const macrosJs = String.raw`
export const MacroCategory = { STATIC: 'static', DYNAMIC: 'dynamic', FLAGS: 'flags' };
export const macros = new Map();
`

const toolCallingJs = String.raw`
export class ToolManager {
  constructor(){ this.tools = new Map(); }
  registerTool(tool){ if (tool && tool.name) this.tools.set(tool.name, tool); }
  unregisterTool(name){ this.tools.delete(name); }
  getTool(name){ return this.tools.get(name); }
  getTools(){ return Array.from(this.tools.values()); }
}
`

const dragdropJs = String.raw`
export class DragAndDropHandler {
  constructor(options){ this.options = options || {}; }
  bind(){}
  unbind(){}
}
`

const naiJs = String.raw`
export const getNovelAnlas = async () => 0;
export const getNovelUnlimitedImageGeneration = async () => false;
export const loadNovelSubscriptionData = async () => ({});
`

const eventsJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
export const eventSource = H.events;
export const event_types = H.event_types;
`

const slashMainJs = String.raw`
const H = window.__FLYAI_EXT_HOST__;
const S = H.slash;
export const executeSlashCommandsWithOptions = S.executeSlashCommandsWithOptions;
export const executeSlashCommandsOnChatInput = S.executeSlashCommandsOnChatInput;
export const setSlashCommandAutoComplete = S.setSlashCommandAutoComplete;
`

const slashParserJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandParser = S.SlashCommandParser;
`

const slashCommandJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommand = S.SlashCommand;
`

const slashArgumentJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandArgument = S.SlashCommandArgument;
export const SlashCommandNamedArgument = S.SlashCommandNamedArgument;
export const ARGUMENT_TYPE = S.ARGUMENT_TYPE;
`

const slashEnumsProviderJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const commonEnumProviders = S.commonEnumProviders;
export const enumIcons = S.enumIcons;
`

const slashEnumValueJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandEnumValue = S.SlashCommandEnumValue;
export const enumTypes = S.enumTypes;
`

const slashClosureJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandClosure = S.SlashCommandClosure;
`

const slashScopeJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandScope = S.SlashCommandScope;
`

const slashAbortJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandAbortController = S.SlashCommandAbortController;
`

const slashDebugJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandDebugController = S.SlashCommandDebugController;
`

const slashExecutorJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandExecutor = S.SlashCommandExecutor;
`

const slashReturnJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const slashCommandReturnHelper = S.slashCommandReturnHelper;
`

const slashBreakJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandBreakPoint = S.SlashCommandBreakPoint;
`

const slashResultJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandClosureResult = S.SlashCommandClosureResult;
`

const slashErrorJs = String.raw`
const S = window.__FLYAI_EXT_HOST__.slash;
export const SlashCommandParserError = S.SlashCommandParserError;
`

const defs: Array<[string, string]> = [
  ['script.js', scriptJs],
  ['scripts/utils.js', utilsJs],
  ['scripts/extensions.js', extensionsJs],
  ['lib.js', libJs],
  ['scripts/popup.js', popupJs],
  ['scripts/constants.js', constantsJs],
  ['scripts/i18n.js', i18nJs],
  ['scripts/RossAscends-mods.js', rossJs],
  ['scripts/secrets.js', secretsJs],
  ['scripts/power-user.js', powerUserJs],
  ['scripts/group-chats.js', groupChatsJs],
  ['scripts/openai.js', openaiJs],
  ['scripts/chats.js', chatsJs],
  ['scripts/tokenizers.js', tokenizersJs],
  ['scripts/preset-manager.js', presetManagerJs],
  ['scripts/world-info.js', worldInfoJs],
  ['scripts/util/AccountStorage.js', accountStorageJs],
  ['scripts/reasoning.js', reasoningJs],
  ['scripts/textgen-settings.js', textgenJs],
  ['scripts/macros/macro-system.js', macrosJs],
  ['scripts/tool-calling.js', toolCallingJs],
  ['scripts/dragdrop.js', dragdropJs],
  ['scripts/nai-settings.js', naiJs],
  ['scripts/events.js', eventsJs],
  ['scripts/slash-commands.js', slashMainJs],
  ['scripts/slash-commands/SlashCommandParser.js', slashParserJs],
  ['scripts/slash-commands/SlashCommand.js', slashCommandJs],
  ['scripts/slash-commands/SlashCommandArgument.js', slashArgumentJs],
  ['scripts/slash-commands/SlashCommandCommonEnumsProvider.js', slashEnumsProviderJs],
  ['scripts/slash-commands/SlashCommandEnumValue.js', slashEnumValueJs],
  ['scripts/slash-commands/SlashCommandClosure.js', slashClosureJs],
  ['scripts/slash-commands/SlashCommandScope.js', slashScopeJs],
  ['scripts/slash-commands/SlashCommandAbortController.js', slashAbortJs],
  ['scripts/slash-commands/SlashCommandDebugController.js', slashDebugJs],
  ['scripts/slash-commands/SlashCommandExecutor.js', slashExecutorJs],
  ['scripts/slash-commands/SlashCommandReturnHelper.js', slashReturnJs],
  ['scripts/slash-commands/SlashCommandBreakPoint.js', slashBreakJs],
  ['scripts/slash-commands/SlashCommandClosureResult.js', slashResultJs],
  ['scripts/slash-commands/SlashCommandParserError.js', slashErrorJs],
]

export const EXTENSION_MODULES: Record<string, string> = Object.fromEntries(defs)

export const EXTENSION_MODULE_PATHS = defs.map(([path]) => path)

/** 每个模块真实导出的名字（与后端扫描表对应）。 */
export const EXTENSION_MODULE_EXPORTS: Record<string, string[]> = {
  'script.js': [
    'eventSource', 'event_types', 'extension_settings', 'extension_prompts', 'characters',
    'chat', 'chat_metadata', 'name2', 'this_chid', 'systemUserName', 'user_avatar',
    'online_status', 'is_send_press', 'main_api', 'CONNECT_API_MAP', 'animation_duration',
    'animation_easing', 'system_message_types', 'streamingProcessor', 'extension_prompt_types',
    'extension_prompt_roles', 'getCurrentChatId', 'getMaxPromptTokens', 'isStreamingEnabled',
    'saveSettingsDebounced', 'substituteParams', 'substituteParamsExtended', 'messageFormatting',
    'setExtensionPrompt', 'getRequestHeaders', 'generateQuietPrompt', 'generateRaw',
    'saveChatConditional', 'reloadCurrentChat', 'getUserAvatar', 'getCharacterAvatar',
    'formatCharacterAvatar', 'getContext',
  ],
  'scripts/utils.js': [
    'clamp', 'collapseSpaces', 'delay', 'escapeHtml', 'escapeRegex', 'onlyUnique',
    'stringFormat', 'uuidv4', 'waitUntilCondition', 'isValidUrl', 'getCharaFilename',
    'getFileExtension', 'sanitizeFilename', 'getSanitizedFilename', 'getSortableDelay',
    'getStringHash', 'deepMerge', 'debounce', 'debounceAsync', 'equalsIgnoreCaseAndAccents',
    'includesIgnoreCaseAndAccents', 'extractAllWords', 'getUniqueName', 'isFalseBoolean',
    'isTrueBoolean', 'regexFromString', 'splitRecursive', 'trimToEndSentence',
    'trimToStartSentence', 'humanizedDateTime', 'isMobile', 'getBase64Async', 'getFileText',
    'download', 'saveBase64AsFile', 'ensureImageFormatSupported', 'findChar', 'flashHighlight',
    'initScrollHeight', 'resetScrollHeight', 'loadFileToDocument', 'setInfoBlock',
    'showFontAwesomePicker', 'getVideoThumbnail',
  ],
  'scripts/extensions.js': [
    'extension_settings', 'extensionNames', 'EMPTY_AUTHOR', 'modules', 'getContext',
    'saveMetadataDebounced', 'renderExtensionTemplateAsync', 'writeExtensionField', 'getApiUrl',
    'doExtrasFetch', 'installExtension', 'deleteExtension', 'getAuthorFromUrl',
    'isOfficialExtension', 'openThirdPartyExtensionMenu', 'ModuleWorkerWrapper',
  ],
  'lib.js': ['Fuse', 'hljs', 'Popper', 'lodash', '_', 'DOMPurify', 'morphdom'],
  'scripts/popup.js': ['POPUP_TYPE', 'POPUP_RESULT', 'Popup', 'callGenericPopup'],
  'scripts/constants.js': [
    'IMAGE_OVERSWIPE', 'MEDIA_DISPLAY', 'MEDIA_REQUEST_TYPE', 'MEDIA_SOURCE', 'MEDIA_TYPE',
    'SCROLL_BEHAVIOR', 'SWIPE_DIRECTION', 'VIDEO_EXTENSIONS', 'debounce_timeout',
  ],
  'scripts/i18n.js': ['translate', 't'],
  'scripts/RossAscends-mods.js': ['dragElement', 'getMessageTimeStamp', 'humanizedDateTime', 'isMobile'],
  'scripts/secrets.js': ['SECRET_KEYS', 'secret_state', 'getSecretLabelById'],
  'scripts/power-user.js': ['power_user', 'collapseNewlines', 'loadMovingUIState', 'performFuzzySearch', 'registerDebugFunction'],
  'scripts/group-chats.js': ['groups', 'hideMutedSprites', 'is_group_generating', 'selected_group'],
  'scripts/openai.js': ['POLLINATIONS_ENDPOINT', 'oai_settings'],
  'scripts/chats.js': ['deleteAttachment', 'deleteMediaFromServer', 'getDataBankAttachments', 'getDataBankAttachmentsForSource', 'getFileAttachment', 'uploadFileAttachmentToServer'],
  'scripts/tokenizers.js': ['tokenizers', 'getFriendlyTokenizerName', 'getTextTokens', 'getTokenCountAsync'],
  'scripts/preset-manager.js': ['getPresetManager'],
  'scripts/world-info.js': ['getSortedEntries'],
  'scripts/util/AccountStorage.js': ['accountStorage'],
  'scripts/reasoning.js': ['removeReasoningFromString', 'updateReasoningUI'],
  'scripts/textgen-settings.js': ['isJsonSchemaSupported', 'textgen_types', 'textgenerationwebui_settings'],
  'scripts/macros/macro-system.js': ['MacroCategory', 'macros'],
  'scripts/tool-calling.js': ['ToolManager'],
  'scripts/dragdrop.js': ['DragAndDropHandler'],
  'scripts/nai-settings.js': ['getNovelAnlas', 'getNovelUnlimitedImageGeneration', 'loadNovelSubscriptionData'],
  'scripts/events.js': ['eventSource', 'event_types'],
  'scripts/slash-commands.js': ['executeSlashCommandsWithOptions', 'executeSlashCommandsOnChatInput', 'setSlashCommandAutoComplete'],
  'scripts/slash-commands/SlashCommandParser.js': ['SlashCommandParser'],
  'scripts/slash-commands/SlashCommand.js': ['SlashCommand'],
  'scripts/slash-commands/SlashCommandArgument.js': ['SlashCommandArgument', 'SlashCommandNamedArgument', 'ARGUMENT_TYPE'],
  'scripts/slash-commands/SlashCommandCommonEnumsProvider.js': ['commonEnumProviders', 'enumIcons'],
  'scripts/slash-commands/SlashCommandEnumValue.js': ['SlashCommandEnumValue', 'enumTypes'],
  'scripts/slash-commands/SlashCommandClosure.js': ['SlashCommandClosure'],
  'scripts/slash-commands/SlashCommandScope.js': ['SlashCommandScope'],
  'scripts/slash-commands/SlashCommandAbortController.js': ['SlashCommandAbortController'],
  'scripts/slash-commands/SlashCommandDebugController.js': ['SlashCommandDebugController'],
  'scripts/slash-commands/SlashCommandExecutor.js': ['SlashCommandExecutor'],
  'scripts/slash-commands/SlashCommandReturnHelper.js': ['slashCommandReturnHelper'],
  'scripts/slash-commands/SlashCommandBreakPoint.js': ['SlashCommandBreakPoint'],
  'scripts/slash-commands/SlashCommandClosureResult.js': ['SlashCommandClosureResult'],
  'scripts/slash-commands/SlashCommandParserError.js': ['SlashCommandParserError'],
}

/** 生成 data: 模块 URL（UTF-8 + base64）。 */
export const moduleDataUrl = (source: string): string => {
  const bytes = new TextEncoder().encode(source)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return `data:text/javascript;base64,${btoa(binary)}`
}

/** import map：把扩展目录里的相对 import 解析到 data: shim。 */
export const buildExtensionImportMap = (origin: string): Record<string, string> => {
  const entries: Record<string, string> = {}
  for (const [path, source] of Object.entries(EXTENSION_MODULES)) {
    const url = moduleDataUrl(source)
    entries[`${origin}/${path}`] = url
    entries[`null/${path}`] = url
  }
  return entries
}
