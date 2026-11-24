"""扩展静态扫描：import 模块/符号缺口 + 兼容等级。

ES module 的命名导入是链接期解析的：只要少一个导出，扩展整体加载失败。
所以这里在安装时就告诉用户“缺什么”，而不是等运行时白屏。
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .models import CompatReport, ExtensionManifest

# 宿主模块 → 本项目 shim 真实导出的符号（必须与前端模块注册表一致）。
HOST_MODULES: Dict[str, Set[str]] = {
    "script.js": {
        "characters", "chat", "chat_metadata", "getCurrentChatId", "getMaxPromptTokens",
        "main_api", "name2", "this_chid", "saveSettingsDebounced", "substituteParams",
        "eventSource", "event_types", "extension_prompt_types", "extension_prompt_roles",
        "setExtensionPrompt", "getRequestHeaders", "generateQuietPrompt", "generateRaw",
        "messageFormatting", "substituteParamsExtended", "saveChatConditional",
        "reloadCurrentChat", "isStreamingEnabled", "is_send_press", "online_status",
        "systemUserName", "user_avatar", "getUserAvatar", "getCharacterAvatar",
        "formatCharacterAvatar", "animation_duration", "animation_easing",
        "CONNECT_API_MAP", "system_message_types", "getContext", "extension_settings",
        "streamingProcessor", "this_chid",
    },
    "scripts/utils.js": {
        "debounce", "debounceAsync", "delay", "escapeHtml", "escapeRegex", "onlyUnique",
        "stringFormat", "uuidv4", "waitUntilCondition", "isValidUrl", "getCharaFilename",
        "getFileExtension", "clamp", "collapseSpaces", "deepMerge",
        "equalsIgnoreCaseAndAccents", "extractAllWords", "download",
        "ensureImageFormatSupported", "findChar", "flashHighlight", "getBase64Async",
        "getFileText", "getSanitizedFilename", "getSortableDelay", "getStringHash",
        "getUniqueName", "getVideoThumbnail", "includesIgnoreCaseAndAccents",
        "initScrollHeight", "isFalseBoolean", "isTrueBoolean", "loadFileToDocument",
        "regexFromString", "resetScrollHeight", "saveBase64AsFile", "setInfoBlock",
        "showFontAwesomePicker", "splitRecursive", "trimToEndSentence",
        "trimToStartSentence", "humanizedDateTime", "isMobile", "sanitizeFilename",
    },
    "scripts/extensions.js": {
        "extension_settings", "extensionNames", "getContext", "EMPTY_AUTHOR",
        "ModuleWorkerWrapper", "deleteExtension", "doExtrasFetch", "getApiUrl",
        "getAuthorFromUrl", "installExtension", "isOfficialExtension", "modules",
        "openThirdPartyExtensionMenu", "renderExtensionTemplateAsync",
        "saveMetadataDebounced", "writeExtensionField",
    },
    "lib.js": {"DOMPurify", "Fuse", "Popper", "hljs", "lodash", "morphdom"},
    "scripts/popup.js": {"POPUP_TYPE", "POPUP_RESULT", "Popup", "callGenericPopup"},
    "scripts/secrets.js": {"SECRET_KEYS", "getSecretLabelById", "secret_state"},
    "scripts/constants.js": {
        "IMAGE_OVERSWIPE", "MEDIA_DISPLAY", "MEDIA_REQUEST_TYPE", "MEDIA_SOURCE",
        "MEDIA_TYPE", "SCROLL_BEHAVIOR", "SWIPE_DIRECTION", "VIDEO_EXTENSIONS",
        "debounce_timeout",
    },
    "scripts/i18n.js": {"t", "translate"},
    "scripts/RossAscends-mods.js": {
        "dragElement", "getMessageTimeStamp", "humanizedDateTime", "isMobile",
    },
    "scripts/power-user.js": {
        "power_user", "collapseNewlines", "loadMovingUIState", "performFuzzySearch",
        "registerDebugFunction",
    },
    "scripts/group-chats.js": {"groups", "hideMutedSprites", "is_group_generating", "selected_group"},
    "scripts/openai.js": {"POLLINATIONS_ENDPOINT", "oai_settings"},
    "scripts/chats.js": {
        "deleteAttachment", "deleteMediaFromServer", "getDataBankAttachments",
        "getDataBankAttachmentsForSource", "getFileAttachment",
        "uploadFileAttachmentToServer",
    },
    "scripts/tokenizers.js": {
        "getFriendlyTokenizerName", "getTextTokens", "getTokenCountAsync", "tokenizers",
    },
    "scripts/preset-manager.js": {"getPresetManager"},
    "scripts/world-info.js": {"getSortedEntries"},
    "scripts/util/AccountStorage.js": {"accountStorage"},
    "scripts/reasoning.js": {"removeReasoningFromString", "updateReasoningUI"},
    "scripts/textgen-settings.js": {
        "isJsonSchemaSupported", "textgen_types", "textgenerationwebui_settings",
    },
    "scripts/macros/macro-system.js": {"MacroCategory", "macros"},
    "scripts/tool-calling.js": {"ToolManager"},
    "scripts/dragdrop.js": {"DragAndDropHandler"},
    "scripts/nai-settings.js": {
        "getNovelAnlas", "getNovelUnlimitedImageGeneration", "loadNovelSubscriptionData",
    },
    "scripts/events.js": {"eventSource", "event_types"},
    "scripts/slash-commands.js": {
        "executeSlashCommandsOnChatInput", "executeSlashCommandsWithOptions",
        "setSlashCommandAutoComplete",
    },
    "scripts/slash-commands/SlashCommandParser.js": {"SlashCommandParser"},
    "scripts/slash-commands/SlashCommand.js": {"SlashCommand"},
    "scripts/slash-commands/SlashCommandArgument.js": {
        "SlashCommandArgument", "SlashCommandNamedArgument", "ARGUMENT_TYPE",
    },
    "scripts/slash-commands/SlashCommandCommonEnumsProvider.js": {
        "commonEnumProviders", "enumIcons",
    },
    "scripts/slash-commands/SlashCommandEnumValue.js": {"SlashCommandEnumValue", "enumTypes"},
    "scripts/slash-commands/SlashCommandClosure.js": {"SlashCommandClosure"},
    "scripts/slash-commands/SlashCommandScope.js": {"SlashCommandScope"},
    "scripts/slash-commands/SlashCommandAbortController.js": {"SlashCommandAbortController"},
    "scripts/slash-commands/SlashCommandDebugController.js": {"SlashCommandDebugController"},
    "scripts/slash-commands/SlashCommandExecutor.js": {"SlashCommandExecutor"},
    "scripts/slash-commands/SlashCommandReturnHelper.js": {"slashCommandReturnHelper"},
    "scripts/slash-commands/SlashCommandBreakPoint.js": {"SlashCommandBreakPoint"},
    "scripts/slash-commands/SlashCommandClosureResult.js": {"SlashCommandClosureResult"},
    "scripts/slash-commands/SlashCommandParserError.js": {"SlashCommandParserError"},
}

# 依赖 ST 主页面 DOM / 布局的模块：L3 明确不加载
DOM_MODULES = {
    "scripts/RossAscends-mods.js",
    "scripts/dragdrop.js",
}
# 可以用宿主能力降级替代的模块：L2（弹窗→宿主 confirm，附件→明确报不支持）
UI_RISK_MODULES = {
    "scripts/popup.js",
    "scripts/chats.js",
}
# 这些符号一旦被 import，说明扩展会直接操作 ST DOM，按 L3 处理
DOM_SYMBOLS = {
    "script.js": {
        "appendMediaToMessage", "updateMessageBlock", "processDroppedFiles",
        "createModelIcon", "activateSendButtons", "deactivateSendButtons",
        "cancelTtsPlay", "getMessageTimeStamp",
    },
}

_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"()]*?\s+from\s+)?|export\s+[^'"()]*?\s+from\s+|import\s*\(\s*)
        ['"]([^'"]+)['"]""",
    re.VERBOSE,
)
_BRACE_IMPORT_RE = re.compile(
    r"""import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]""", re.VERBOSE
)
_JS_SUFFIX = (".js", ".mjs")


def _iter_js(files_dir: Path) -> Iterable[Path]:
    for path in files_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in _JS_SUFFIX:
            yield path


def _normalize(path: str, importer_rel: str, ext_name: str) -> Optional[str]:
    """把扩展里的 import 路径归一化成宿主模块路径；扩展内部模块返回 None。

    不同扩展的目录深度不一样（有的多一层 public/），所以不能只靠 ../ 计数：
    先按相对路径解析，再用“已知宿主模块的路径后缀”做最长匹配。
    """
    raw = (path or "").strip()
    if not raw or raw.startswith("data:") or "://" in raw:
        return None
    if raw.startswith("/"):
        resolved = posixpath.normpath(raw.lstrip("/"))
    else:
        base = posixpath.dirname(importer_rel)
        resolved = posixpath.normpath(posixpath.join(base, raw))
    resolved = resolved.split("?")[0].split("#")[0]
    prefix = "scripts/extensions/third-party/%s/" % ext_name
    if resolved.startswith(prefix):
        return None
    if resolved in HOST_MODULES:
        return resolved
    for known in HOST_MODULES:
        if resolved.endswith("/" + known):
            return known
    return resolved


def _named_imports(source: str) -> List[Tuple[str, List[str]]]:
    out: List[Tuple[str, List[str]]] = []
    for match in _BRACE_IMPORT_RE.finditer(source):
        symbols = []
        for part in match.group(1).split(","):
            name = part.strip().split(" as ")[0].strip()
            if name and name != "default" and re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", name):
                symbols.append(name)
        out.append((match.group(2), symbols))
    return out


def scan_extension(files_dir: Path, manifest: ExtensionManifest) -> CompatReport:
    report = CompatReport(declared_compat=bool(manifest.flyai.get("compat")))
    importer_prefix = "scripts/extensions/third-party/%s/" % manifest.name

    modules: Dict[str, Dict] = {}
    missing_modules: Set[str] = set()
    missing_symbols: List[Dict[str, str]] = []
    ui_risk: Set[str] = set()
    dom_risk: Set[str] = set()

    for path in _iter_js(files_dir):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        report.files_scanned += 1
        rel = importer_prefix + path.relative_to(files_dir).as_posix()
        imports: Dict[str, List[str]] = {}
        for mod, symbols in _named_imports(source):
            imports.setdefault(mod, []).extend(symbols)
        for mod in _IMPORT_RE.findall(source):
            imports.setdefault(mod, [])
        for raw_mod, symbols in imports.items():
            host = _normalize(raw_mod, rel, manifest.name)
            if not host:
                continue
            item = modules.setdefault(host, {"path": host, "status": "ok", "missing": []})
            if host not in HOST_MODULES:
                item["status"] = "missing"
                missing_modules.add(host)
                continue
            available = HOST_MODULES[host]
            for symbol in symbols:
                if symbol in DOM_SYMBOLS.get(host, set()):
                    dom_risk.add(host)
                if symbol not in available:
                    item["status"] = "partial"
                    if symbol not in item["missing"]:
                        item["missing"].append(symbol)
                    missing_symbols.append({"module": host, "symbol": symbol})
            if host in UI_RISK_MODULES:
                ui_risk.add(host)
            if host in DOM_MODULES:
                dom_risk.add(host)

    report.modules = sorted(modules.values(), key=lambda item: item["path"])
    report.missing_modules = sorted(missing_modules)
    # 去重（同一符号多文件引用）
    seen = set()
    unique: List[Dict[str, str]] = []
    for item in missing_symbols:
        key = (item["module"], item["symbol"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    report.missing_symbols = unique
    report.ui_risk = sorted(ui_risk)

    if manifest.compat == "ui" or dom_risk:
        report.level = "L3"
        report.ui_risk = sorted(ui_risk | dom_risk)
    elif report.ui_risk:
        report.level = "L2"
    else:
        report.level = "L1"
    return report


def is_loadable(manifest: ExtensionManifest, report: CompatReport) -> bool:
    """L3 明确不加载；缺模块/符号允许加载但由管理面板提示缺口。"""
    # L3（ST DOM 依赖）明确不加载；L2 允许加载但由管理面板提示降级。
    if manifest.compat == "ui" or report.level == "L3":
        return False
    return True
