/**
 * 扩展宿主 iframe 的全局 shim（非模块，先于模块注册表执行）。
 *
 * 设计边界：
 * - 不暴露父页面 DOM/Cookie/localStorage；
 * - 所有宿主数据通过 postMessage RPC；
 * - 网络默认拒绝，仅允许宿主代理；
 * - 只实现 L1 逻辑型扩展需要的最小 ST 形状。
 */

export const HOST_GLOBAL_SOURCE = String.raw`
(function(){
  var PARENT = window.parent;
  var seq = 0;
  var pending = Object.create(null);
  var NS = 'flyai-ext';

  var EVENTS = [
    'APP_READY','EXTRAS_CONNECTED','GENERATION_STARTED','GENERATION_AFTER_COMMANDS',
    'GENERATION_STOPPED','GENERATION_ENDED','MESSAGE_SENT','MESSAGE_RECEIVED',
    'MESSAGE_EDITED','MESSAGE_DELETED','MESSAGE_SWIPED','MESSAGE_SWIPE_DELETED',
    'USER_MESSAGE_RENDERED','CHARACTER_MESSAGE_RENDERED','CHAT_CHANGED','CHAT_CREATED',
    'CHAT_DELETED','GROUP_CHAT_DELETED','SETTINGS_LOADED','SETTINGS_UPDATED',
    'EXTENSIONS_FIRST_LOAD','EXTENSION_SETTINGS_LOADED','WORLDINFO_UPDATED',
    'WORLDINFO_SETTINGS_UPDATED','CHARACTER_EDITED','CHARACTER_DUPLICATED',
    'CHARACTER_DELETED','CHARACTER_RENAMED','CHARACTER_RENAMED_FROM_FIELD',
    'CHARACTER_PAGE_LOADED','CHARACTER_FIRST_MESSAGE_SELECTED',
    'CHARACTER_GROUP_OVERLAY_STATE_CHANGE','GROUP_UPDATED','MOVABLE_PANELS_RESET',
    'ONLINE_STATUS_CHANGED','CHAT_LOADED','CHAT_BRANCH_CREATED','CHAT_BRANCH_SWITCHED',
    'BEFORE_MESSAGE_SENT','MESSAGE_UPDATED','MESSAGE_FILE_EMBEDDED','MESSAGE_REASONING_EDITED',
    'MESSAGE_REASONING_DELETED','TOOL_CALLS_PERFORMED','TOOL_CALLS_RENDERED'
  ];
  var event_types = {};
  for (var i=0;i<EVENTS.length;i++) event_types[EVENTS[i]] = EVENTS[i];

  function EventBus(){
    this.listeners = Object.create(null);
    this.first = Object.create(null);
  }
  EventBus.prototype._add = function(event, handler, once, front){
    var key = String(event);
    var list = this.listeners[key] || (this.listeners[key] = []);
    var item = { fn: handler, once: !!once };
    if (front) list.unshift(item); else list.push(item);
    return { stop: this.off.bind(this, key, handler) };
  };
  EventBus.prototype.on = function(event, handler){ return this._add(event, handler, false, false); };
  EventBus.prototype.once = function(event, handler){ return this._add(event, handler, true, false); };
  EventBus.prototype.makeFirst = function(event, handler){ return this._add(event, handler, false, true); };
  EventBus.prototype.makeLast = function(event, handler){ return this._add(event, handler, false, false); };
  EventBus.prototype.off = function(event, handler){
    var key = String(event);
    var list = this.listeners[key];
    if (!list) return;
    if (!handler) { delete this.listeners[key]; return; }
    this.listeners[key] = list.filter(function(item){ return item.fn !== handler; });
  };
  EventBus.prototype.removeListener = EventBus.prototype.off;
  EventBus.prototype.clearEvent = function(event){ delete this.listeners[String(event)]; };
  EventBus.prototype.clearListener = EventBus.prototype.off;
  EventBus.prototype.clearAll = function(){ this.listeners = Object.create(null); };
  EventBus.prototype.emit = function(event){
    var key = String(event);
    var args = Array.prototype.slice.call(arguments, 1);
    var list = (this.listeners[key] || []).slice();
    var results = [];
    for (var i=0;i<list.length;i++){
      var item = list[i];
      if (item.once) this.off(key, item.fn);
      try {
        var out = item.fn.apply(null, args);
        results.push(Promise.resolve(out).catch(function(e){ console.error(e); }));
      } catch (e) { console.error(e); }
    }
    return Promise.all(results);
  };
  EventBus.prototype.emitAndWait = EventBus.prototype.emit;

  var events = new EventBus();
  window.eventSource = events;
  window.event_types = event_types;

  function post(type, payload){
    try { PARENT.postMessage(Object.assign({ v:1, ns:NS, type:type }, payload || {}), '*'); } catch (e) {}
  }
  function request(method, params){
    var id = 'r' + (++seq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pending[id];
        reject(new Error('flyai-ext RPC timeout: ' + method));
      }, 30000);
      pending[id] = { resolve: resolve, reject: reject, timer: timer };
      post('rpc', { id: id, method: method, params: params || {} });
    });
  }
  window.addEventListener('message', function(event){
    if (event.source !== PARENT) return;
    var data = event.data;
    if (!data || data.ns !== NS) return;
    if (data.type === 'rpc-result'){
      var item = pending[data.id];
      if (!item) return;
      delete pending[data.id];
      clearTimeout(item.timer);
      if (data.error) item.reject(new Error(data.error.message || 'RPC failed'));
      else item.resolve(data.result);
    } else if (data.type === 'event' && data.event){
      events.emit(data.event, data.payload);
    } else if (data.type === 'settings'){
      if (data.settings && typeof data.settings === 'object') H.settings = data.settings;
    }
  });

  var BOOT = window.__FLYAI_EXT_BOOT__ || {};
  var H = {
    boot: BOOT,
    state: BOOT.state || {},
    settings: BOOT.settings || {},
    extension_prompts: {},
    request: request,
    events: events,
    event_types: event_types,
    ready: function(){ post('ready', {}); },
    log: function(message){ post('log', { message: String(message) }); },
    error: function(message){ post('log', { message: String(message), error: true }); }
  };
  window.__FLYAI_EXT_HOST__ = H;
  window.SillyTavern = { getContext: function(){ return H.state.context || {}; } };

  // ---- 设置持久化 ----
  var settingsTimer = 0;
  H.saveSettingsDebounced = function(){
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(function(){
      request('settings.save', { settings: H.settings }).catch(function(e){ console.warn(e); });
    }, 500);
  };
  Object.defineProperty(window, 'extension_settings', { get: function(){ return H.settings; }, configurable: true });
  window.saveSettingsDebounced = H.saveSettingsDebounced;
  window.extension_settings = H.settings;
  window.extension_prompts = H.extension_prompts;

  // ---- 宏替换 / 文本处理 ----
  H.substituteParams = function(text){
    var state = H.state || {};
    return String(text == null ? '' : text)
      .replace(/\{\{char\}\}/gi, state.name2 || '')
      .replace(/\{\{user\}\}/gi, state.user_name || 'User')
      .replace(/\{\{time\}\}/gi, new Date().toLocaleTimeString())
      .replace(/\{\{date\}\}/gi, new Date().toLocaleDateString())
      .replace(/\{\{getvar::([^}]+)\}\}/gi, function(_, key){ return String((H.state.variables || {})[key] ?? ''); });
  };
  H.messageFormatting = function(text){
    return String(text == null ? '' : text)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/\n/g,'<br>');
  };

  // ---- 头像 ----
  H.getUserAvatar = function(){ return H.state.user_avatar || 'user-default.png'; };
  H.getCharacterAvatar = function(){ return H.state.character_avatar || ''; };
  H.formatCharacterAvatar = function(avatar){ return avatar || H.state.character_avatar || ''; };

  // ---- 生成 ----
  async function generate(method, args){
    var payload = Object.assign({}, args || {});
    if (payload.quietPrompt != null && payload.quiet_prompt == null) payload.quiet_prompt = payload.quietPrompt;
    if (payload.prompt != null && payload.quiet_prompt == null) payload.quiet_prompt = String(payload.prompt);
    if (!payload.quiet_prompt && payload.messages) payload.quiet_prompt = '';
    return request(method, payload);
  }
  H.generateQuietPrompt = function(args){
    if (typeof args === 'string') args = { quietPrompt: args };
    return generate('generate.quiet', args || {});
  };
  H.generateRaw = function(args){ return generate('generate.raw', args || {}); };

  // ---- Popup ----
  var POPUP_TYPE = { TEXT:0, CONFIRM:1, INPUT:2, DISPLAY:3, CUSTOM:4 };
  var POPUP_RESULT = { AFFIRMATIVE:1, CANCELLED:0 };
  function callGenericPopup(type, message, defaultValue){
    return request('ui.popup', { type: type, message: String(message == null ? '' : message), defaultValue: defaultValue })
      .then(function(result){ return result; });
  }
  function Popup(type, message){
    this.type = type; this.message = message;
    this.show = function(){ return callGenericPopup(type, message); };
    this.complete = function(){};
    this.result = POPUP_RESULT.CANCELLED;
  }
  H.Popup = Popup; H.callGenericPopup = callGenericPopup;
  H.POPUP_TYPE = POPUP_TYPE; H.POPUP_RESULT = POPUP_RESULT;
  window.callGenericPopup = callGenericPopup;
  window.Popup = Popup;

  // ---- toastr ----
  function toast(level){
    return function(message){
      request('ui.toast', { level: level, message: String(message == null ? '' : message) }).catch(function(){});
    };
  }
  window.toastr = { success: toast('success'), info: toast('info'), warning: toast('warning'), error: toast('error') };

  // ---- fetch 桥 ----
  var origFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function(input, init){
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var absolute;
    try { absolute = new URL(url, BOOT.baseUrl || location.href).href; } catch (e) { absolute = String(url); }
    var sameOrigin = false;
    try { sameOrigin = new URL(absolute).origin === location.origin; } catch (e) {}
    var method = (init && init.method) || 'GET';
    var headers = {};
    try { Object.assign(headers, (init && init.headers) || {}); } catch (e) {}
    var body = init && typeof init.body === 'string' ? init.body : '';
    var rpc = sameOrigin ? 'http.fetch' : 'net.fetch';
    return request(rpc, { url: absolute, method: method, headers: headers, body: body }).then(function(res){
      var text = res && res.text != null ? res.text : '';
      return new Response(text, { status: (res && res.status) || 200, headers: (res && res.headers) || {} });
    });
  };
  window.XMLHttpRequest = function(){ throw new Error('XMLHttpRequest is not supported in extension sandbox'); };

  // ---- Slash 子系统 ----
  var ARGUMENT_TYPE = {
    STRING:'string', NUMBER:'number', BOOLEAN:'boolean', VARIABLE_NAME:'variable',
    CLOSURE:'closure', SUBCOMMAND:'subcommand', LIST:'list'
  };
  var enumTypes = { STRING:'string', NUMBER:'number', BOOLEAN:'boolean' };
  function SlashCommandEnumValue(value, description){
    this.value = value; this.description = description || ''; this.type = 'string'; this.icon = '';
  }
  function SlashCommandArgument(props){ Object.assign(this, props || {}); }
  SlashCommandArgument.fromProps = function(props){ return new SlashCommandArgument(props); };
  function SlashCommandNamedArgument(props){ Object.assign(this, props || {}); }
  SlashCommandNamedArgument.fromProps = function(props){ return new SlashCommandNamedArgument(props); };
  function SlashCommand(props){ Object.assign(this, props || {}); this.name = String((props||{}).name || '').toLowerCase(); }
  SlashCommand.fromProps = function(props){ return new SlashCommand(props); };
  function SlashCommandScope(){ this.variables = {}; }
  SlashCommandScope.prototype.setVariable = function(name, value){ this.variables[String(name)] = value; };
  SlashCommandScope.prototype.getVariable = function(name){ return this.variables[String(name)]; };
  function SlashCommandAbortController(){ this.aborted = false; }
  SlashCommandAbortController.prototype.abort = function(){ this.aborted = true; };
  function SlashCommandDebugController(){ this.enabled = false; }
  function SlashCommandExecutor(props){ Object.assign(this, props || {}); }
  function SlashCommandClosure(fn){ this.fn = typeof fn === 'function' ? fn : function(){ return ''; }; }
  function SlashCommandClosureResult(value){ this.value = value; }
  function SlashCommandBreakPoint(){}
  function SlashCommandParserError(message){ this.message = message; }
  var slashCommandReturnHelper = {
    pipe: function(value){ return value; },
    toast: function(value){ toast('info')(value); return value; },
    chat: function(value){ return value; },
    popup: function(value){ return callGenericPopup(POPUP_TYPE.TEXT, value); },
    none: function(){ return ''; }
  };
  var commonEnumProviders = {};
  var enumIcons = {};

  function tokenize(text){
    var out = []; var cur = ''; var quote = '';
    for (var i=0;i<text.length;i++){
      var ch = text[i];
      if (quote){
        if (ch === quote){ quote = ''; continue; }
        if (ch === '\\' && i+1 < text.length){ cur += text[++i]; continue; }
        cur += ch;
      } else if (ch === '"' || ch === "'") { quote = ch; }
      else if (/\s/.test(ch)) { if (cur) { out.push(cur); cur=''; } }
      else cur += ch;
    }
    if (cur) out.push(cur);
    return out;
  }
  function parseArgs(tokens){
    var named = {}; var unnamed = [];
    for (var i=0;i<tokens.length;i++){
      var t = tokens[i]; var eq = t.indexOf('=');
      if (eq > 0){ named[t.slice(0,eq)] = t.slice(eq+1); } else unnamed.push(t);
    }
    return { named: named, unnamed: unnamed };
  }
  async function executeOne(segment, scope, pipe){
    var tokens = tokenize(segment.trim());
    if (!tokens.length) return pipe;
    var name = tokens[0].replace(/^\//,'').toLowerCase();
    var cmd = SlashCommandParser.commands[name];
    if (!cmd) throw new Error('Unknown slash command: ' + name);
    var args = parseArgs(tokens.slice(1));
    var ctx = { pipe: pipe, scope: scope, unnamed: args.unnamed, named: args.named };
    var result = await cmd.callback(args.named, args.unnamed, ctx);
    return result == null ? '' : result;
  }
  var SlashCommandParser = {
    commands: Object.create(null),
    addCommandObject: function(cmd){ if (cmd && cmd.name) this.commands[cmd.name] = cmd; },
    getCommand: function(name){ return this.commands[String(name).replace(/^\//,'').toLowerCase()]; },
    parse: tokenize
  };
  async function executeSlashCommandsWithOptions(text, options){
    var scope = new SlashCommandScope();
    var parts = String(text || '').split('|');
    var pipe = '';
    for (var i=0;i<parts.length;i++) pipe = await executeOne(parts[i], scope, pipe);
    return pipe;
  }
  async function executeSlashCommandsOnChatInput(text){ return executeSlashCommandsWithOptions(text); }
  function setSlashCommandAutoComplete(){}
  H.slash = {
    SlashCommandParser: SlashCommandParser, SlashCommand: SlashCommand,
    SlashCommandArgument: SlashCommandArgument, SlashCommandNamedArgument: SlashCommandNamedArgument,
    ARGUMENT_TYPE: ARGUMENT_TYPE, SlashCommandEnumValue: SlashCommandEnumValue, enumTypes: enumTypes,
    commonEnumProviders: commonEnumProviders, enumIcons: enumIcons,
    SlashCommandScope: SlashCommandScope, SlashCommandAbortController: SlashCommandAbortController,
    SlashCommandDebugController: SlashCommandDebugController, SlashCommandExecutor: SlashCommandExecutor,
    SlashCommandClosure: SlashCommandClosure, SlashCommandClosureResult: SlashCommandClosureResult,
    SlashCommandBreakPoint: SlashCommandBreakPoint, SlashCommandParserError: SlashCommandParserError,
    slashCommandReturnHelper: slashCommandReturnHelper,
    executeSlashCommandsWithOptions: executeSlashCommandsWithOptions,
    executeSlashCommandsOnChatInput: executeSlashCommandsOnChatInput,
    setSlashCommandAutoComplete: setSlashCommandAutoComplete
  };

  // 注册到宿主，让聊天输入框能以 /cmd 执行
  SlashCommandParser.addCommandObject = function(cmd){
    if (cmd && cmd.name){
      this.commands[cmd.name] = cmd;
      request('slash.register', {
        name: cmd.name, help: cmd.helpString || cmd.help || '',
        aliases: cmd.aliases || []
      }).catch(function(){});
    }
  };
  H.executeSlash = executeSlashCommandsWithOptions;

  // ---- L2 挂件注册 ----
  H.ui = {
    registerMessageWidget: function(widget){
      if (!widget || !widget.id) return;
      request('ui.widget.register', {
        slot: 'message',
        id: String(widget.id),
        label: String(widget.label || widget.id),
        command: String(widget.command || ''),
        title: String(widget.title || widget.label || '')
      }).catch(function(){});
    },
    registerHeaderWidget: function(widget){
      if (!widget || !widget.id) return;
      request('ui.widget.register', {
        slot: 'header',
        id: String(widget.id),
        label: String(widget.label || widget.id),
        command: String(widget.command || ''),
        title: String(widget.title || widget.label || '')
      }).catch(function(){});
    }
  };

  // 错误上报
  window.addEventListener('error', function(event){
    post('log', { message: String(event.message || 'error'), error: true });
  });
  window.addEventListener('unhandledrejection', function(event){
    post('log', { message: String((event.reason && event.reason.message) || event.reason), error: true });
  });
})();
`;
