/* 酒馆兼容层共享核心（纯 ES5，注入两个 iframe shim 之前）。
 *
 * cardApp.ts 和 authorRuntime.ts 的消息/变量/世界书/事件实现都从这里取，
 * 避免两个运行时继续分叉。这里不碰 DOM、不发请求，只做数据转换。
 */
(function () {
  'use strict';

  var ST = {};

  // ---- 消息范围 ----

  function toIndex(value, length) {
    var n = Number(value);
    if (!isFinite(n)) return null;
    if (n < 0) n = length + n;
    return n;
  }

  ST.resolveRange = function (range, length) {
    if (!length) return [];
    var text = range == null ? String(length - 1) : String(range).trim();
    text = text.replace(/\{\{\s*lastMessageId\s*\}\}/gi, String(length - 1));
    var m = text.match(/^(-?\d+)\s*-\s*(-?\d+)$/);
    var start;
    var end;
    if (m) {
      start = toIndex(m[1], length);
      end = toIndex(m[2], length);
    } else {
      start = toIndex(text, length);
      end = start;
    }
    if (start == null || end == null) return [];
    if (start > end) {
      var swap = start;
      start = end;
      end = swap;
    }
    start = Math.max(0, start);
    end = Math.min(length - 1, end);
    if (start > end) return [];
    var out = [];
    for (var i = start; i <= end; i += 1) out.push(i);
    return out;
  };

  ST.roleOf = function (raw, charName) {
    if (!raw || typeof raw !== 'object') return 'assistant';
    if (raw.role === 'user' || raw.role === 'assistant' || raw.role === 'system') return raw.role;
    if (raw.is_user === true) return 'user';
    if (raw.is_system === true) return 'system';
    return 'assistant';
  };

  ST.messageText = function (raw) {
    if (!raw || typeof raw !== 'object') return '';
    if (raw.text != null) return String(raw.text);
    if (raw.mes != null) return String(raw.mes);
    if (raw.message != null) return String(raw.message);
    return '';
  };

  ST.messageData = function (raw) {
    if (!raw || typeof raw !== 'object') return {};
    if (raw.data && typeof raw.data === 'object' && !Array.isArray(raw.data)) return raw.data;
    if (raw.statData && typeof raw.statData === 'object') return { stat_data: raw.statData };
    return {};
  };

  ST.chatMessage = function (raw, index, opts, includeSwipes) {
    opts = opts || {};
    var role = ST.roleOf(raw, opts.charName);
    var text = ST.messageText(raw);
    var isHidden = raw && (raw.is_hidden === true || raw.hidden === true ||
      (raw.extra && raw.extra.is_hidden === true));
    var out = {
      message_id: index,
      name: (raw && raw.name) || (role === 'user' ? opts.userName : opts.charName) || '',
      role: role,
      is_hidden: !!isHidden,
      message: text,
      data: ST.messageData(raw),
      extra: raw && raw.extra && typeof raw.extra === 'object' ? raw.extra : {},
    };
    if (includeSwipes) {
      var swipes = raw && Array.isArray(raw.swipes) && raw.swipes.length
        ? raw.swipes.slice()
        : [text];
      var swipeId = raw && typeof raw.swipe_id === 'number' ? raw.swipe_id : 0;
      if (swipeId < 0 || swipeId >= swipes.length) swipeId = 0;
      var swipesData = raw && Array.isArray(raw.swipes_data)
        ? raw.swipes_data.slice()
        : swipes.map(function (_, i) {
            return i === swipeId ? ST.messageData(raw) : {};
          });
      var swipesInfo = raw && Array.isArray(raw.swipes_info)
        ? raw.swipes_info.slice()
        : swipes.map(function () { return {}; });
      out.swipe_id = swipeId;
      out.swipes = swipes;
      out.swipes_data = swipesData;
      out.swipes_info = swipesInfo;
    }
    return out;
  };

  ST.getChatMessages = function (chat, range, options, opts) {
    var list = Array.isArray(chat) ? chat : [];
    options = options || {};
    var role = options.role || 'all';
    var hideState = options.hide_state || 'all';
    var includeSwipes = options.include_swipes === true;
    if (Array.isArray(range)) {
      // 兼容旧调用：一次取多个楼层，按传入顺序返回
      var many = [];
      for (var a = 0; a < range.length; a += 1) {
        var part = ST.getChatMessages(list, range[a], options, opts);
        for (var b = 0; b < part.length; b += 1) many.push(part[b]);
      }
      return many;
    }
    var rangeText = range == null ? '' : String(range).trim();
    // TavernHelper 支持 'latest' / 'last' / 'last_user' / 'last_char'
    if (/^(latest|last|last_user|last_char)$/i.test(rangeText)) {
      var wantRole = /last_user/i.test(rangeText) ? 'user'
        : /last_char/i.test(rangeText) ? 'assistant' : '';
      for (var r = list.length - 1; r >= 0; r -= 1) {
        if (wantRole && ST.roleOf(list[r], opts && opts.charName) !== wantRole) continue;
        range = r;
        break;
      }
      if (typeof range !== 'number') return [];
    }
    var indexes = ST.resolveRange(range, list.length);
    var out = [];
    for (var i = 0; i < indexes.length; i += 1) {
      var index = indexes[i];
      var raw = list[index];
      if (!raw) continue;
      var item = ST.chatMessage(raw, index, opts, includeSwipes);
      if (role !== 'all' && item.role !== role) continue;
      if (hideState === 'hidden' && !item.is_hidden) continue;
      if (hideState === 'unhidden' && item.is_hidden) continue;
      out.push(item);
    }
    return out;
  };

  // ---- 变量路径 / 深合并 ----

  var UNSAFE_KEYS = { __proto__: true, constructor: true, prototype: true };

  function pathParts(path) {
    if (Array.isArray(path)) return path.map(String).filter(Boolean);
    return String(path == null ? '' : path).split('.').filter(Boolean);
  }

  ST.getPath = function (value, path) {
    var parts = pathParts(path);
    var cur = value;
    for (var i = 0; i < parts.length; i += 1) {
      if (UNSAFE_KEYS[parts[i]]) return undefined;
      if (cur == null || typeof cur !== 'object') return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  };

  ST.setPath = function (value, path, next) {
    var parts = pathParts(path);
    if (!parts.length || UNSAFE_KEYS[parts[0]]) return value;
    var cur = value && typeof value === 'object' ? value : {};
    for (var i = 0; i < parts.length - 1; i += 1) {
      var key = parts[i];
      if (UNSAFE_KEYS[key]) return value;
      if (!cur[key] || typeof cur[key] !== 'object' || Array.isArray(cur[key])) cur[key] = {};
      cur = cur[key];
    }
    var last = parts[parts.length - 1];
    if (!UNSAFE_KEYS[last]) cur[last] = next;
    return value;
  };

  ST.deletePath = function (value, path) {
    var parts = pathParts(path);
    if (!parts.length || UNSAFE_KEYS[parts[0]]) return false;
    var cur = value;
    for (var i = 0; i < parts.length - 1; i += 1) {
      if (UNSAFE_KEYS[parts[i]]) return false;
      if (cur == null || typeof cur !== 'object') return false;
      cur = cur[parts[i]];
    }
    if (cur == null || typeof cur !== 'object') return false;
    var last = parts[parts.length - 1];
    if (UNSAFE_KEYS[last] || !Object.prototype.hasOwnProperty.call(cur, last)) return false;
    delete cur[last];
    return true;
  };

  function clone(value) {
    if (value == null || typeof value !== 'object') return value;
    try { return JSON.parse(JSON.stringify(value)); } catch (e) { return value; }
  }

  function mergeInto(target, source, sourceWins) {
    if (!source || typeof source !== 'object') return target;
    for (var key in source) {
      if (!Object.prototype.hasOwnProperty.call(source, key) || UNSAFE_KEYS[key]) continue;
      var incoming = source[key];
      var existing = target[key];
      if (Array.isArray(incoming)) {
        target[key] = clone(incoming);
      } else if (incoming && typeof incoming === 'object') {
        var baseChild = existing && typeof existing === 'object' && !Array.isArray(existing)
          ? existing
          : {};
        if (sourceWins) {
          target[key] = mergeInto(baseChild, incoming, true);
        } else {
          target[key] = mergeInto(clone(incoming), baseChild, true);
        }
      } else if (sourceWins || !Object.prototype.hasOwnProperty.call(target, key)) {
        target[key] = incoming;
      }
    }
    return target;
  }

  ST.merge = function (base, source, sourceWins) {
    var out = base && typeof base === 'object' && !Array.isArray(base) ? clone(base) : {};
    return mergeInto(out, source, sourceWins !== false);
  };

  /** insertVariables：source 里已有值优先，不覆盖 base。 */
  ST.mergeKeep = function (base, source) {
    var out = clone(source && typeof source === 'object' ? source : {});
    return mergeInto(out, base, true);
  };

  // ---- 世界书 ----

  function keysOf(entry) {
    if (entry && entry.strategy && Array.isArray(entry.strategy.keys)) return entry.strategy.keys;
    if (entry && Array.isArray(entry.key)) return entry.key;
    if (entry && typeof entry.key === 'string' && entry.key) return [entry.key];
    if (entry && Array.isArray(entry.keys)) return entry.keys;
    return [];
  }

  ST.worldbookEntryList = function (book) {
    var source = book;
    if (Array.isArray(book)) source = { entries: book };
    var entries = source && source.entries;
    var list = [];
    if (Array.isArray(entries)) {
      list = entries.slice();
    } else if (entries && typeof entries === 'object') {
      for (var uid in entries) {
        if (!Object.prototype.hasOwnProperty.call(entries, uid)) continue;
        list.push(entries[uid] || {});
      }
    }
    var out = [];
    for (var i = 0; i < list.length; i += 1) {
      var entry = list[i] || {};
      var keys = keysOf(entry);
      var name = entry.name || entry.title || entry.comment || keys[0] ||
        String(entry.uid == null ? i + 1 : entry.uid);
      var enabled = entry.enabled;
      if (typeof enabled !== 'boolean') enabled = entry.disable !== true;
      var constant = entry.constant;
      if (typeof constant !== 'boolean') {
        constant = entry.pinned === true || !!(entry.strategy && entry.strategy.type === 'constant');
      }
      var secondary = entry.secondary_keys || entry.keysecondary ||
        (entry.strategy && entry.strategy.keys_secondary && entry.strategy.keys_secondary.keys) || [];
      var logic = entry.selective_logic ||
        (entry.strategy && entry.strategy.keys_secondary && entry.strategy.keys_secondary.logic) ||
        'and_any';
      var position = entry.position;
      var positionType = entry.position_type || 'at_depth';
      var depth = entry.depth;
      var order = entry.order;
      var role = entry.role;
      if (position && typeof position === 'object') {
        positionType = position.type || 'at_depth';
        depth = position.depth;
        order = position.order;
        role = position.role;
      } else if (typeof entry.position === 'string') {
        positionType = entry.position_type ||
          (entry.position === 'depth' ? 'at_depth' : 'before');
      } else if (typeof entry.position === 'number') {
        positionType = ['before', 'after', 'ANTop', 'ANBottom',
          'at_depth', 'EMTop', 'EMBottom', 'outlet'][entry.position] || 'before';
      }
      var item = clone(entry);
      item.uid = Number(entry.uid == null ? i + 1 : entry.uid);
      item.name = String(name);
      item.enabled = enabled;
      item.constant = constant;
      item.keys = keys.map(String);
      item.key = keys.map(String);
      item.keysecondary = secondary.map(String);
      item.strategy = {
        type: entry.strategy && entry.strategy.type
          ? entry.strategy.type
          : (constant ? 'constant' : 'selective'),
        keys: keys.map(String),
        keys_secondary: {
          logic: logic,
          keys: secondary.map(String),
        },
        scan_depth: entry.strategy && entry.strategy.scan_depth != null
          ? entry.strategy.scan_depth
          : (entry.scanDepth != null ? entry.scanDepth : 'same_as_global'),
      };
      item.position = {
        type: positionType,
        role: role === 'user' || role === 'assistant' || role === 'system'
          ? role
          : (entry.role === 1 ? 'user' : entry.role === 2 ? 'assistant' : 'system'),
        depth: Number(depth == null ? 4 : depth),
        order: Number(order == null ? 100 : order),
      };
      item.content = String(entry.content == null ? '' : entry.content);
      item.probability = Number(entry.probability == null ? 100 : entry.probability);
      item.selective_logic = logic;
      item.case_sensitive = entry.case_sensitive === true || entry.caseSensitive === true;
      item.match_whole_words = entry.match_whole_words === true || entry.matchWholeWords === true;
      item.recursion = entry.recursion || {
        prevent_incoming: !!entry.preventRecursion,
        prevent_outgoing: !!entry.excludeRecursion,
        delay_until: entry.delayUntilRecursion === true
          ? null
          : (entry.delayUntilRecursion == null ? null : Number(entry.delayUntilRecursion)),
      };
      item.effect = entry.effect || {
        sticky: entry.sticky == null ? null : Number(entry.sticky),
        cooldown: entry.cooldown == null ? null : Number(entry.cooldown),
        delay: entry.delay == null ? null : Number(entry.delay),
      };
      item.extra = entry.extra || {};
      item.comment = String(entry.comment || item.name);
      item.disable = !item.enabled;
      out.push(item);
    }
    out.sort(function (a, b) { return a.uid - b.uid; });
    return out;
  };

  ST.worldbookFromList = function (list, name) {
    var entries = {};
    var source = Array.isArray(list) ? list : [];
    for (var i = 0; i < source.length; i += 1) {
      var entry = clone(source[i] || {});
      var uid = Number(entry.uid);
      if (!isFinite(uid) || uid < 0) uid = i + 1;
      while (entries[uid]) uid += 1;
      var keys = keysOf(entry);
      var strategy = entry.strategy || {};
      var position = entry.position && typeof entry.position === 'object' ? entry.position : {};
      var constant = entry.constant;
      if (typeof constant !== 'boolean') constant = strategy.type === 'constant';
      var enabled = entry.enabled;
      if (typeof enabled !== 'boolean') enabled = entry.disable !== true;
      entries[uid] = {
        uid: uid,
        comment: String(entry.name || entry.title || entry.comment || keys[0] || uid),
        content: String(entry.content == null ? '' : entry.content),
        key: keys.map(String),
        keysecondary: (strategy.keys_secondary && Array.isArray(strategy.keys_secondary.keys)
          ? strategy.keys_secondary.keys
          : (entry.keysecondary || entry.secondary_keys || [])).map(String),
        secondary_keys: (strategy.keys_secondary && Array.isArray(strategy.keys_secondary.keys)
          ? strategy.keys_secondary.keys
          : (entry.secondary_keys || entry.keysecondary || [])).map(String),
        selective_logic: entry.selective_logic ||
          (strategy.keys_secondary && strategy.keys_secondary.logic) || 'and_any',
        case_sensitive: entry.case_sensitive === true || entry.caseSensitive === true,
        match_whole_words: entry.match_whole_words === true || entry.matchWholeWords === true,
        constant: constant,
        disable: !enabled,
        enabled: enabled,
        selective: entry.selective !== false,
        scanDepth: strategy.scan_depth != null ? strategy.scan_depth : (entry.scanDepth == null ? null : entry.scanDepth),
        position: position.type === 'at_depth' ? 0 : 1,
        position_type: entry.position_type || position.type || 'before',
        positionType: entry.position_type || position.type || 'before',
        depth: Number(position.depth == null ? (entry.depth == null ? 4 : entry.depth) : position.depth),
        order: Number(position.order == null ? (entry.order == null ? 100 : entry.order) : position.order),
        role: position.role === 'user' ? 1 : position.role === 'assistant' ? 2 : 0,
        probability: Number(entry.probability == null ? 100 : entry.probability),
        recursion: entry.recursion || {
          prevent_incoming: !!entry.prevent_recursion,
          prevent_outgoing: !!entry.exclude_recursion,
          delay_until: entry.delay_until_recursion == null ? null : Number(entry.delay_until_recursion),
        },
        effect: entry.effect || {
          sticky: entry.sticky == null ? null : Number(entry.sticky),
          cooldown: entry.cooldown == null ? null : Number(entry.cooldown),
          delay: entry.delay == null ? null : Number(entry.delay),
        },
        extra: entry.extra || {},
      };
    }
    return { name: String(name || ''), entries: entries };
  };

  ST.worldbookHostEntries = function (book) {
    var list = ST.worldbookEntryList(book);
    return list.map(function (entry) {
      return {
        // 保留酒馆 uid：卡按 uid 读写条目，行号重排会让回读核对失败
        uid: typeof entry.uid === 'number' && isFinite(entry.uid)
          ? Number(entry.uid)
          : null,
        title: entry.name || entry.comment || String(entry.uid),
        content: entry.content || '',
        pinned: entry.constant === true,
        keys: (entry.strategy && entry.strategy.keys) || entry.key || [],
        position: entry.position && entry.position.type === 'at_depth' ? 'depth' : 'system',
        position_type: entry.position ? entry.position.type :
          (entry.position_type || 'before'),
        depth: entry.position ? entry.position.depth : 4,
        insert_order: entry.position ? entry.position.order : 100,
        role: entry.position ? entry.position.role : 'system',
        probability: entry.probability == null ? 100 : entry.probability,
        // 额外保真字段：后端 LoreEntry 目前会忽略，后续扩展
        enabled: entry.enabled !== false,
        constant: entry.constant === true,
        selective: entry.strategy ? entry.strategy.type !== 'constant' : true,
        keysecondary: entry.keysecondary || [],
        secondary_keys: entry.keysecondary || [],
        selective_logic: entry.selective_logic || 'and_any',
        scanDepth: entry.strategy ? entry.strategy.scan_depth : null,
        case_sensitive: entry.case_sensitive === true,
        match_whole_words: entry.match_whole_words === true,
        sticky: entry.effect ? entry.effect.sticky : null,
        cooldown: entry.effect ? entry.effect.cooldown : null,
        delay: entry.effect ? entry.effect.delay : null,
        prevent_recursion: !!(entry.recursion && entry.recursion.prevent_incoming),
        exclude_recursion: !!(entry.recursion && entry.recursion.prevent_outgoing),
        delay_until_recursion: entry.recursion ? entry.recursion.delay_until : null,
      };
    });
  };

  // ---- 事件常量 ----

  ST.tavernEvents = {
    APP_READY: 'app_ready', EXTRAS_CONNECTED: 'extras_connected', MESSAGE_SWIPED: 'message_swiped',
    MESSAGE_SENT: 'message_sent', MESSAGE_RECEIVED: 'message_received', MESSAGE_EDITED: 'message_edited',
    MESSAGE_DELETED: 'message_deleted', MESSAGE_UPDATED: 'message_updated',
    MESSAGE_FILE_EMBEDDED: 'message_file_embedded', MESSAGE_REASONING_EDITED: 'message_reasoning_edited',
    MESSAGE_REASONING_DELETED: 'message_reasoning_deleted', MESSAGE_SWIPE_DELETED: 'message_swipe_deleted',
    MORE_MESSAGES_LOADED: 'more_messages_loaded', IMPERSONATE_READY: 'impersonate_ready',
    CHAT_CHANGED: 'chat_id_changed', GENERATION_AFTER_COMMANDS: 'GENERATION_AFTER_COMMANDS',
    GENERATION_STARTED: 'generation_started', GENERATION_STOPPED: 'generation_stopped',
    GENERATION_ENDED: 'generation_ended', SD_PROMPT_PROCESSING: 'sd_prompt_processing',
    EXTENSIONS_FIRST_LOAD: 'extensions_first_load', EXTENSION_SETTINGS_LOADED: 'extension_settings_loaded',
    SETTINGS_LOADED: 'settings_loaded', SETTINGS_UPDATED: 'settings_updated',
    MOVABLE_PANELS_RESET: 'movable_panels_reset', SETTINGS_LOADED_BEFORE: 'settings_loaded_before',
    SETTINGS_LOADED_AFTER: 'settings_loaded_after', CHATCOMPLETION_SOURCE_CHANGED: 'chatcompletion_source_changed',
    CHATCOMPLETION_MODEL_CHANGED: 'chatcompletion_model_changed', OAI_PRESET_CHANGED_BEFORE: 'oai_preset_changed_before',
    OAI_PRESET_CHANGED_AFTER: 'oai_preset_changed_after', OAI_PRESET_EXPORT_READY: 'oai_preset_export_ready',
    OAI_PRESET_IMPORT_READY: 'oai_preset_import_ready', WORLDINFO_SETTINGS_UPDATED: 'worldinfo_settings_updated',
    WORLDINFO_UPDATED: 'worldinfo_updated', CHARACTER_EDITOR_OPENED: 'character_editor_opened',
    CHARACTER_EDITED: 'character_edited', CHARACTER_PAGE_LOADED: 'character_page_loaded',
    USER_MESSAGE_RENDERED: 'user_message_rendered', CHARACTER_MESSAGE_RENDERED: 'character_message_rendered',
    FORCE_SET_BACKGROUND: 'force_set_background', CHAT_DELETED: 'chat_deleted', CHAT_CREATED: 'chat_created',
    GENERATE_BEFORE_COMBINE_PROMPTS: 'generate_before_combine_prompts',
    GENERATE_AFTER_COMBINE_PROMPTS: 'generate_after_combine_prompts', GENERATE_AFTER_DATA: 'generate_after_data',
    WORLD_INFO_ACTIVATED: 'world_info_activated', TEXT_COMPLETION_SETTINGS_READY: 'text_completion_settings_ready',
    CHAT_COMPLETION_SETTINGS_READY: 'chat_completion_settings_ready',
    CHAT_COMPLETION_PROMPT_READY: 'chat_completion_prompt_ready',
    CHARACTER_FIRST_MESSAGE_SELECTED: 'character_first_message_selected', CHARACTER_DELETED: 'characterDeleted',
    CHARACTER_DUPLICATED: 'character_duplicated', CHARACTER_RENAMED: 'character_renamed',
    CHARACTER_RENAMED_IN_PAST_CHAT: 'character_renamed_in_past_chat',
    SMOOTH_STREAM_TOKEN_RECEIVED: 'stream_token_received', STREAM_TOKEN_RECEIVED: 'stream_token_received',
    STREAM_REASONING_DONE: 'stream_reasoning_done', FILE_ATTACHMENT_DELETED: 'file_attachment_deleted',
    WORLDINFO_FORCE_ACTIVATE: 'worldinfo_force_activate', OPEN_CHARACTER_LIBRARY: 'open_character_library',
    ONLINE_STATUS_CHANGED: 'online_status_changed', IMAGE_SWIPED: 'image_swiped',
    CONNECTION_PROFILE_LOADED: 'connection_profile_loaded', CONNECTION_PROFILE_CREATED: 'connection_profile_created',
    CONNECTION_PROFILE_DELETED: 'connection_profile_deleted', CONNECTION_PROFILE_UPDATED: 'connection_profile_updated',
    TOOL_CALLS_PERFORMED: 'tool_calls_performed', TOOL_CALLS_RENDERED: 'tool_calls_rendered',
    CHARACTER_MANAGEMENT_DROPDOWN: 'charManagementDropdown', SECRET_WRITTEN: 'secret_written',
    SECRET_DELETED: 'secret_deleted', SECRET_ROTATED: 'secret_rotated', SECRET_EDITED: 'secret_edited',
    PRESET_CHANGED: 'preset_changed', PRESET_DELETED: 'preset_deleted', PRESET_RENAMED: 'preset_renamed',
    PRESET_RENAMED_BEFORE: 'preset_renamed_before', MAIN_API_CHANGED: 'main_api_changed',
    WORLDINFO_ENTRIES_LOADED: 'worldinfo_entries_loaded', WORLDINFO_SCAN_DONE: 'worldinfo_scan_done',
    MEDIA_ATTACHMENT_DELETED: 'media_attachment_deleted'
  };

  ST.iframeEvents = {
    MESSAGE_IFRAME_RENDER_STARTED: 'message_iframe_render_started',
    MESSAGE_IFRAME_RENDER_ENDED: 'message_iframe_render_ended',
    GENERATION_REQUESTED: 'js_generation_requested',
    GENERATION_STARTED: 'js_generation_started',
    STREAM_TOKEN_RECEIVED_FULLY: 'js_stream_token_received_fully',
    STREAM_TOKEN_RECEIVED_INCREMENTALLY: 'js_stream_token_received_incrementally',
    GENERATION_ENDED: 'js_generation_ended'
  };

  // ---- 沙箱 IndexedDB 桩 ----
  // 不透明源 iframe 里 indexedDB.open 会抛 SecurityError。酒馆助手扩展常以
  // “indexedDB 存在”作为启用日志/媒体库的条件（约会大作战小手机），之后整个
  // 流程卡在 rejected promise 上。这里提供进程内实现（open/createObjectStore/
  // transaction/get/put/delete/clear 常用子集），数据只在当前 iframe 生命周期内
  // 有效；扩展刷新后会重新从聊天文件（chat_metadata.variables）回读权威状态。
  ST.installIndexedDbShim = function (win, options) {
    if (!win || !win.indexedDB) return false;
    var persist = options && typeof options === 'object' ? options : null;
    var databases = {};
    var loaded = false;
    var saveTimer = null;
    var nextId = 0;
    // 扩展（例如约会大作战手机）会把 journal 当成本机权威存储。进程内实现
    // 刷新即丢，所以允许宿主提供 load/save：把库内容落到 chat_metadata。
    // 首次 open 时才读，避免 shim 初始化早于会话状态初始化。
    function ensureLoaded() {
      if (loaded) return;
      loaded = true;
      if (!persist || typeof persist.load !== 'function') return;
      try {
        var data = persist.load();
        if (data && typeof data === 'object') {
          var parsed = JSON.parse(JSON.stringify(data));
          for (var dbName in parsed) {
            if (!Object.prototype.hasOwnProperty.call(parsed, dbName)) continue;
            var raw = parsed[dbName] && typeof parsed[dbName] === 'object' ? parsed[dbName] : {};
            var db = makeDatabase(String(dbName));
            db.version = Number(raw.version) || 0;
            var stores = raw.stores && typeof raw.stores === 'object' ? raw.stores : {};
            for (var storeName in stores) {
              if (!Object.prototype.hasOwnProperty.call(stores, storeName)) continue;
              db.stores[String(storeName)] =
                stores[storeName] && typeof stores[storeName] === 'object' ? stores[storeName] : {};
            }
            databases[String(dbName)] = db;
          }
        }
      } catch (e) {}
    }
    function scheduleSave() {
      if (!persist || typeof persist.save !== 'function') return;
      if (saveTimer) return;
      var flush = function () {
        saveTimer = null;
        try {
          var text = JSON.stringify(databases, function (key, value) {
            if (value && typeof value === 'object') {
              if (typeof win.Blob === 'function' && value instanceof win.Blob) return undefined;
              if (typeof win.ArrayBuffer === 'function' && value instanceof win.ArrayBuffer) {
                return undefined;
              }
            }
            return value;
          });
          if (text) persist.save(JSON.parse(text));
        } catch (e) {}
      };
      if (typeof win.setTimeout === 'function') saveTimer = win.setTimeout(flush, 200);
      else flush();
    }
    function defer(fn) {
      if (typeof win.setTimeout === 'function') win.setTimeout(fn, 0);
      else fn();
    }
    function abortError(message) {
      var err = new Error(message);
      try { err.name = 'UnknownError'; } catch (e) {}
      return err;
    }
    function newRequest() {
      return {
        result: undefined,
        error: null,
        readyState: 'pending',
        onsuccess: null,
        onerror: null
      };
    }
    function succeed(req, value) {
      req.result = value;
      req.readyState = 'done';
      if (typeof req.onsuccess === 'function') req.onsuccess({ target: req });
    }
    function fail(req, err) {
      req.error = err;
      req.readyState = 'done';
      if (typeof req.onerror === 'function') req.onerror({ target: req });
    }
    function cloneValue(value) {
      if (value == null || typeof value !== 'object') return value;
      if (typeof win.Blob === 'function' && value instanceof win.Blob) return value;
      if (typeof win.ArrayBuffer === 'function' && value instanceof win.ArrayBuffer) {
        return value.slice(0);
      }
      try { return JSON.parse(JSON.stringify(value)); } catch (e) { return value; }
    }
    function storeMap(db, name) {
      if (!db.stores[name]) db.stores[name] = {};
      return db.stores[name];
    }
    function makeStore(map, queue) {
      function op(run) {
        var req = newRequest();
        queue(function () {
          try { run(req); } catch (e) { fail(req, e); }
        });
        return req;
      }
      return {
        get: function (key) {
          return op(function (req) {
            var k = String(key);
            succeed(req, Object.prototype.hasOwnProperty.call(map, k)
              ? cloneValue(map[k])
              : undefined);
          });
        },
        put: function (value, key) {
          return op(function (req) {
            var k = key == null ? String(++nextId) : String(key);
            map[k] = cloneValue(value);
            scheduleSave();
            succeed(req, k);
          });
        },
        add: function (value, key) {
          return op(function (req) {
            var k = key == null ? String(++nextId) : String(key);
            if (Object.prototype.hasOwnProperty.call(map, k)) {
              fail(req, abortError('Key already exists in the object store.'));
              return;
            }
            map[k] = cloneValue(value);
            scheduleSave();
            succeed(req, k);
          });
        },
        delete: function (key) {
          return op(function (req) {
            delete map[String(key)];
            scheduleSave();
            succeed(req, undefined);
          });
        },
        clear: function () {
          return op(function (req) {
            for (var k in map) {
              if (Object.prototype.hasOwnProperty.call(map, k)) delete map[k];
            }
            scheduleSave();
            succeed(req, undefined);
          });
        }
      };
    }
    function makeTransaction(db, storeName) {
      var tx = {
        mode: 'readonly',
        error: null,
        oncomplete: null,
        onabort: null,
        onerror: null
      };
      var tasks = [];
      var running = false;
      var closed = false;
      function drain() {
        if (running || closed) return;
        if (!tasks.length) {
          closed = true;
          defer(function () { if (typeof tx.oncomplete === 'function') tx.oncomplete({ target: tx }); });
          return;
        }
        running = true;
        var task = tasks.shift();
        defer(function () {
          try { task(); } catch (e) {}
          running = false;
          drain();
        });
      }
      function queue(task) {
        if (closed) return;
        tasks.push(task);
        drain();
      }
      tx.abort = function () {
        if (closed) return;
        closed = true;
        tx.error = abortError('Transaction aborted.');
        if (typeof tx.onabort === 'function') tx.onabort({ target: tx });
      };
      tx.objectStore = function (name) {
        return makeStore(storeMap(db, String(name)), queue);
      };
      return tx;
    }
    function makeDatabase(name) {
      var db = { name: name, version: 0, stores: {}, onversionchange: null };
      db.objectStoreNames = {
        contains: function (storeName) {
          return !!db.stores[String(storeName)];
        },
        item: function (index) {
          var keys = [];
          for (var k in db.stores) {
            if (Object.prototype.hasOwnProperty.call(db.stores, k)) keys.push(k);
          }
          return keys[index] || null;
        }
      };
      db.createObjectStore = function (storeName) {
        storeMap(db, String(storeName));
        return {};
      };
      db.transaction = function (storeName) {
        return makeTransaction(db, String(storeName));
      };
      db.close = function () {};
      return db;
    }
    var factory = {
      open: function (name, version) {
        ensureLoaded();
        var req = newRequest();
        var dbName = String(name);
        var wanted = Number(version) || 1;
        defer(function () {
          var db = databases[dbName];
          var created = !db;
          if (!db) db = databases[dbName] = makeDatabase(dbName);
          var oldVersion = db.version;
          req.result = db;
          if (created || oldVersion < wanted) {
            db.version = wanted;
            if (typeof req.onupgradeneeded === 'function') {
              try {
                req.onupgradeneeded({
                  target: req,
                  oldVersion: oldVersion,
                  newVersion: wanted
                });
              } catch (e) {
                fail(req, e);
                return;
              }
            }
          }
          succeed(req, db);
        });
        return req;
      },
      deleteDatabase: function (name) {
        var req = newRequest();
        defer(function () {
          delete databases[String(name)];
          scheduleSave();
          succeed(req, undefined);
        });
        return req;
      },
      cmp: function (a, b) {
        if (a === b) return 0;
        return a < b ? -1 : 1;
      }
    };
    try {
      Object.defineProperty(win, 'indexedDB', { value: factory, configurable: true });
      return true;
    } catch (e) {
      return false;
    }
  };

  globalThis.__FLYAI_ST_CORE__ = ST;
})();
