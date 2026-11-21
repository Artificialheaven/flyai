"""酒馆卡 MVU（Magic Variable Update）的原生实现。

SillyTavern 的 MVU 扩展靠卡内 JavaScript 解析变量更新；本项目**绝不执行**
卡内 JS，只用原生代码实现同一套数据语义：

- InitVar：从角色卡 ``table`` 中标题含 ``[InitVar]`` 的条目提取 YAML，去
  code fence 后深合并为初始 ``stat_data``；
- 更新：从助手消息提取 ``<UpdateVariable>…<JSONPatch>…</JSONPatch>`` 块，
  原生实现 RFC 6902（add/remove/replace/move/copy/test）；
- **原子应用**：一个更新块内任一 op 失败则整块丢弃并返回 warning，不半更新；
- 路径归一化：``/stat_data/x``、``/x`` 与点路径 ``stat_data.x`` 都落到
  ``stat_data`` 根上；
- prompt 侧兜底：``<UpdateVariable>`` 块只从发给模型的副本中剥离，存储原文
  保留给展示侧正则渲染（卡内脚本 3/4 依赖它做美化）。

宏 ``{{format_message_variable::stat_data.<path>}}`` 由 ``tavern.py`` 展开。
"""

from __future__ import annotations

import copy
import json
import re
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..tavern import lenient_json_loads

try:  # PyYAML 在 requirements.txt 中固定版本；缺失时 MVU 自动降级为禁用
    import yaml  # type: ignore
except Exception:  # pragma: no cover - 环境缺 YAML
    yaml = None  # type: ignore

MAX_INIT_YAML = 256 * 1024
MAX_UPDATE_BLOCK = 512 * 1024
MAX_UPDATE_BLOCKS = 32

_INITVAR_TITLE_RE = re.compile(r"\[InitVar\]", re.IGNORECASE)
# 部分酒馆卡把 MVU 声明放在世界书条目标题里（如 [mvu_update]变量输出格式），
# 而不是 [InitVar] 条目或 regex_scripts；标题命中即说明这张卡声明了 MVU。
_MVU_DECL_TITLE_RE = re.compile(r"\[(?:InitVar|mvu_update)\]", re.IGNORECASE)
_FENCE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
_UPDATE_RE = re.compile(
    r"<UpdateVariable\b[^>]*>([\s\S]*?)</UpdateVariable\s*>", re.IGNORECASE
)
_PATCH_TAG_RE = re.compile(r"<JSONPatch\b[^>]*>([\s\S]*?)</JSONPatch\s*>", re.IGNORECASE)
# 内联初值：开场白里的 <InitVar>…</InitVar>（可能被 <UpdateVariable> 包住）
_INLINE_INIT_RE = re.compile(
    r"<InitVar\b[^>]*>([\s\S]*?)</InitVar\s*>", re.IGNORECASE
)


class PatchError(ValueError):
    """JSON Patch 应用失败（整块更新将被丢弃）。"""


# ---- InitVar YAML ----


def _fence_blocks(text: str) -> List[str]:
    """提取全部 code fence 内容；无 fence 时整段视为一块。

    卡内 [InitVar] 可能是单个 fenced YAML，也可能拆成多段 fence 叠加，
    需逐块解析后再深合并（拼成一段会出现重复键被后者覆盖）。
    """
    blocks = _FENCE_RE.findall(text or "")
    return blocks if blocks else [text or ""]


def _strip_fence(text: str) -> str:
    """提取并拼接全部 code fence 内容；无 fence 时原样返回。"""
    return "\n".join(_fence_blocks(text))


def _entry_title(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("title") or "")
    return str(getattr(entry, "title", "") or "")


def _entry_content(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("content") or "")
    return str(getattr(entry, "content", "") or "")


def deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """深合并两个字典：嵌套 dict 递归合并，其余类型由 later 覆盖。"""
    for key, value in (extra or {}).items():
        cur = base.get(key)
        if isinstance(cur, dict) and isinstance(value, dict):
            deep_merge(cur, value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _stat_root(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """YAML 顶层可能直接是 stat_data，也可能包一层 ``stat_data:``。"""
    inner = parsed.get("stat_data")
    if isinstance(inner, dict):
        return inner
    return parsed


def _normalize_yaml_block(raw: str) -> str:
    """规范化内联 YAML 块：统一换行、去行尾空白、去首尾空行、整体去缩进。

    不能先 ``strip()``：内联块常以 ``\\r\\n  `` 开头，缩进会被整块削掉一行，
    而后续行仍保留原缩进，PyYAML 会把它当成非法缩进。
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return textwrap.dedent(text.strip("\n"))


_INIT_MACRO_RE = re.compile(r"\{\{\s*(user|char|group)\s*\}\}", re.IGNORECASE)
_INIT_MACRO_KEY_RE = re.compile(r"^\{\{\s*(user|char|group)\s*\}\}$", re.IGNORECASE)


def _macro_value(kind: str, char_name: str, user_name: str,
                 group_names: Optional[Sequence[str]]) -> str:
    """与提示词侧 ``render_tavern_macros`` 同名的回退值，保证两边一致。"""
    if kind == "char":
        return char_name or "角色"
    if kind == "user":
        return user_name or "用户"
    return "、".join(group_names or [])


def _expand_init_macros(text: str, char_name: str = "", user_name: str = "",
                        group_names: Optional[Sequence[str]] = None) -> str:
    """展开 InitVar YAML 里的 ``{{user}}/{{char}}/{{group}}``。

    MVU 状态键必须与提示词里模型看到的键一致。提示词侧世界书渲染会把
    ``{{user}}`` 展开成用户名，而 InitVar 若不展开，本地 stat_data 会停在
    字面量 ``{{user}}`` 上；模型按提示词里的名字生成 patch 路径后，整块
    更新会因“路径不存在”被原子丢弃（面板数值因此不再变化）。
    """
    if not isinstance(text, str) or "{{" not in text:
        return text or ""

    def _repl(match: "re.Match[str]") -> str:
        return _macro_value(match.group(1).lower(), char_name, user_name,
                            group_names)

    return _INIT_MACRO_RE.sub(_repl, text)


def _merge_inline_init(text: str, merged: Dict[str, Any],
                       warnings: List[str]) -> None:
    """把开场白内联 ``<InitVar>`` 块合并进 ``merged``（后者覆盖前者）。"""
    if not text or not _INLINE_INIT_RE.search(text):
        return
    if yaml is None:
        warnings.append("缺少 PyYAML，内联 <InitVar> 无法解析")
        return
    for match in _INLINE_INIT_RE.finditer(text):
        for raw in _fence_blocks(match.group(1)):
            raw = _normalize_yaml_block(raw)
            if not raw:
                continue
            if len(raw) > MAX_INIT_YAML:
                warnings.append("<InitVar> 超过 %d 字节，已忽略" % MAX_INIT_YAML)
                continue
            try:
                parsed = yaml.safe_load(raw)
            except yaml.YAMLError as exc:  # type: ignore[union-attr]
                warnings.append("<InitVar> YAML 解析失败：%s" % exc)
                continue
            if not isinstance(parsed, dict):
                warnings.append("<InitVar> 顶层不是对象，已忽略")
                continue
            deep_merge(merged, _stat_root(parsed))


def load_init_var(table: Sequence[Any], first_mes: str = "", *,
                  char_name: str = "", user_name: str = "",
                  group_names: Optional[Sequence[str]] = None,
                  ) -> Tuple[Dict[str, Any], List[str]]:
    """合并 ``[InitVar]`` 世界书条目与开场白内联 ``<InitVar>``。返回 (stat_data, warnings)。

    合并顺序：先 table 条目，后开场白内联块——内联更贴近本局实际开场，覆盖前者。
    ``char_name``/``user_name`` 与提示词侧宏替换使用同一组名字；InitVar 里的
    ``{{user}}`` 等标记必须先展开再解析 YAML，否则状态键与模型写的 patch 路径
    不一致（见 ``_expand_init_macros``）。
    """
    warnings: List[str] = []
    merged: Dict[str, Any] = {}
    for entry in table or []:
        if not _INITVAR_TITLE_RE.search(_entry_title(entry)):
            continue
        if yaml is None:
            warnings.append("缺少 PyYAML，[InitVar] 无法解析")
            return {}, warnings
        for raw in _fence_blocks(_entry_content(entry)):
            raw = _expand_init_macros(
                raw, char_name=char_name, user_name=user_name,
                group_names=group_names,
            ).strip()
            if not raw:
                continue
            if len(raw) > MAX_INIT_YAML:
                warnings.append("[InitVar] 超过 %d 字节，已忽略" % MAX_INIT_YAML)
                continue
            try:
                parsed = yaml.safe_load(raw)
            except yaml.YAMLError as exc:  # type: ignore[union-attr]
                warnings.append("[InitVar] YAML 解析失败：%s" % exc)
                continue
            if not isinstance(parsed, dict):
                warnings.append("[InitVar] 顶层不是对象，已忽略")
                continue
            deep_merge(merged, _stat_root(parsed))
    _merge_inline_init(
        _expand_init_macros(
            first_mes or "", char_name=char_name, user_name=user_name,
            group_names=group_names,
        ),
        merged, warnings,
    )
    return merged, warnings


def _script_texts(character: Any) -> List[str]:
    out: List[str] = []
    for raw in getattr(character, "regex_scripts", None) or []:
        if not isinstance(raw, dict):
            continue
        for key in ("findRegex", "replaceString", "scriptName"):
            val = raw.get(key)
            if isinstance(val, str):
                out.append(val)
    return out


def card_uses_mvu(character: Any) -> bool:
    """卡内是否声明了 MVU（InitVar/mvu_update 条目、内联 InitVar 或更新块脚本）。

    只用 ``<InitVar>`` 作为内联强信号：大量非 MVU 卡的教程文本会提到
    ``<UpdateVariable>``，单凭它判断误报率高。
    """
    for entry in getattr(character, "table", None) or []:
        if _MVU_DECL_TITLE_RE.search(_entry_title(entry)):
            return True
    if _INLINE_INIT_RE.search(getattr(character, "first_mes", "") or ""):
        return True
    for text in _script_texts(character):
        lowered = text.lower()
        if "updatevariable" in lowered or "jsonpatch" in lowered:
            return True
    return False


def selected_greeting(character: Any, index: Any) -> str:
    """按会话记录的 greeting_index 取开场白原文；越界/空回落到 first_mes。"""
    try:
        idx = int(index or 0)
    except (TypeError, ValueError):
        idx = 0
    if idx > 0:
        alts = list(getattr(character, "alternate_greetings", None) or [])
        if 0 <= idx - 1 < len(alts):
            text = str(alts[idx - 1] or "").strip()
            if text:
                return text
    return getattr(character, "first_mes", "") or ""


def build_initial_state(character: Any, *, user_name: str = "",
                        group_names: Optional[Sequence[str]] = None,
                        first_mes: Optional[str] = None,
                        ) -> Tuple[Dict[str, Any], List[str]]:
    """为角色卡构造会话级 MVU 初值。

    返回 (state, warnings)，state 形如
    ``{"enabled", "initialized", "char_id", "initial", "stat_data"}``。
    ``user_name`` 需与提示词侧一致：没有用户角色卡时传空串，由宏回退成
    ``"用户"``，与 ``render_tavern_macros`` 相同。``first_mes`` 用于覆盖
    角色卡默认开场白（会话选择了备用问候时，其内联 ``<InitVar>`` 也要生效）。
    """
    warnings: List[str] = []
    if not card_uses_mvu(character):
        return {"enabled": False}, warnings
    if first_mes is None:
        first_mes = getattr(character, "first_mes", "") or ""
    init_data, init_warnings = load_init_var(
        getattr(character, "table", None) or [],
        first_mes,
        char_name=getattr(character, "name", "") or "",
        user_name=user_name,
        group_names=group_names,
    )
    warnings.extend(init_warnings)
    return {
        "enabled": True,
        "initialized": True,
        "char_id": str(getattr(character, "id", "") or ""),
        "initial": copy.deepcopy(init_data),
        "stat_data": copy.deepcopy(init_data),
    }, warnings


# ---- JSON Patch（RFC 6902）----


def _decode_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _parse_path(path: Any) -> List[str]:
    if not isinstance(path, str):
        raise PatchError("path 必须是字符串")
    path = path.strip()
    if not path:
        return []
    if path.startswith("/"):
        return [_decode_token(p) for p in path[1:].split("/")]
    # 容忍卡内/模型常用的点路径写法（stat_data.a.b）
    if "." in path and "/" not in path and "[" not in path:
        return [p for p in path.split(".") if p]
    raise PatchError("path 必须以 / 开头：%r" % path)


def normalize_path(path: Any) -> List[str]:
    """``/stat_data/x`` 与 ``/x`` 都落到 stat_data 根上。"""
    parts = _parse_path(path)
    if parts and parts[0] == "stat_data":
        parts = parts[1:]
    return parts


def _index(token: str, seq: List[Any], allow_end: bool) -> int:
    if token == "-":
        if allow_end:
            return len(seq)
        raise PatchError("数组下标不能为 '-'")
    if not token.isdigit():
        raise PatchError("数组下标非法：%r" % token)
    idx = int(token)
    limit = len(seq) if allow_end else len(seq) - 1
    if idx > limit:
        raise PatchError("数组下标越界：%d" % idx)
    return idx


def _parent(root: Any, parts: List[str]) -> Any:
    cur = root
    for part in parts:
        if isinstance(cur, dict):
            if part not in cur:
                raise PatchError("路径不存在：%r" % part)
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[_index(part, cur, allow_end=False)]
        else:
            raise PatchError("路径穿过标量值")
    return cur


def _get(root: Any, parts: List[str]) -> Any:
    return _parent(root, parts) if parts else root


def _numeric(value: Any, name: str) -> Any:
    """把增量操作的操作数转成数字；容忍模型输出的数字字符串（"+5"、"3"）。

    bool 是 int 的子类，显式排除，避免 ``true`` 被当成 1 静默累加。
    """
    if isinstance(value, bool):
        raise PatchError("%s 不是数字：%r" % (name, value))
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip()
    try:
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        return float(text)
    except ValueError:
        raise PatchError("%s 不是数字：%r" % (name, value))


def _apply_one(root: Any, op: Dict[str, Any]) -> Any:
    name = op.get("op")
    path = normalize_path(op.get("path"))
    if name == "add":
        if not path:
            return copy.deepcopy(op.get("value"))
        parent = _parent(root, path[:-1])
        key = path[-1]
        if isinstance(parent, dict):
            parent[key] = copy.deepcopy(op.get("value"))
        elif isinstance(parent, list):
            parent.insert(_index(key, parent, allow_end=True), copy.deepcopy(op.get("value")))
        else:
            raise PatchError("add 的父节点不是容器")
    elif name == "remove":
        if not path:
            raise PatchError("不能删除根节点")
        parent = _parent(root, path[:-1])
        key = path[-1]
        if isinstance(parent, dict):
            if key not in parent:
                raise PatchError("remove 的键不存在：%r" % key)
            del parent[key]
        elif isinstance(parent, list):
            del parent[_index(key, parent, allow_end=False)]
        else:
            raise PatchError("remove 的父节点不是容器")
    elif name == "replace":
        if not path:
            return copy.deepcopy(op.get("value"))
        parent = _parent(root, path[:-1])
        key = path[-1]
        if isinstance(parent, dict):
            if key not in parent:
                raise PatchError("replace 的键不存在：%r" % key)
            parent[key] = copy.deepcopy(op.get("value"))
        elif isinstance(parent, list):
            parent[_index(key, parent, allow_end=False)] = copy.deepcopy(op.get("value"))
        else:
            raise PatchError("replace 的父节点不是容器")
    elif name == "delta":
        # 卡内自定义扩展：在原数值上累加（RFC 6902 没有增量语义）。
        # 目标键不存在时从 0 起算——InitVar 定义的是全集，键缺失多半是模型
        # 新增的合法字段，整块丢弃反而会丢光同块内其它正确改动。
        if not path:
            raise PatchError("delta 的路径不能是根节点")
        parent = _parent(root, path[:-1])
        key = path[-1]
        step = _numeric(op.get("value"), "delta")
        if isinstance(parent, dict):
            parent[key] = _numeric(parent.get(key, 0), "delta 目标") + step
        elif isinstance(parent, list):
            idx = _index(key, parent, allow_end=False)
            parent[idx] = _numeric(parent[idx], "delta 目标") + step
        else:
            raise PatchError("delta 的父节点不是容器")
    elif name == "insert":
        # 卡内自定义扩展：dict 按 key 写入，list 按下标插入（"-" 追加）。
        if not path:
            raise PatchError("insert 的路径不能是根节点")
        parent = _parent(root, path[:-1])
        key = path[-1]
        if isinstance(parent, dict):
            parent[key] = copy.deepcopy(op.get("value"))
        elif isinstance(parent, list):
            parent.insert(_index(key, parent, allow_end=True), copy.deepcopy(op.get("value")))
        else:
            raise PatchError("insert 的父节点不是容器")
    elif name == "move":
        src = normalize_path(op.get("from"))
        if not src:
            raise PatchError("move 的 from 不能是根节点")
        if path[: len(src)] == src and len(path) > len(src):
            raise PatchError("move 不能移入自身的子路径")
        value = copy.deepcopy(_get(root, src))
        root = _apply_one(root, {"op": "remove", "path": op.get("from")})
        root = _apply_one(root, {"op": "add", "path": op.get("path"), "value": value})
    elif name == "copy":
        src = normalize_path(op.get("from"))
        value = copy.deepcopy(_get(root, src))
        root = _apply_one(root, {"op": "add", "path": op.get("path"), "value": value})
    elif name == "test":
        if _get(root, path) != op.get("value"):
            raise PatchError("test 失败：%r" % (op.get("path"),))
    else:
        raise PatchError("不支持的 op：%r" % (name,))
    return root


def apply_patch(document: Any, operations: Any) -> Dict[str, Any]:
    """在 stat_data 副本上原子应用一组 JSON Patch op，失败抛 PatchError。"""
    if not isinstance(operations, list):
        raise PatchError("JSONPatch 必须是数组")
    if not operations:
        return copy.deepcopy(document if isinstance(document, dict) else {})
    root: Any = copy.deepcopy(document if isinstance(document, dict) else {})
    for op in operations:
        if not isinstance(op, dict):
            raise PatchError("op 必须是对象")
        root = _apply_one(root, op)
    if not isinstance(root, dict):
        # MVU 根必须是对象；被整体替换成标量视为非法
        raise PatchError("更新后根节点不是对象")
    return root


# ---- <UpdateVariable> 解析与应用 ----


def extract_patches(text: Optional[str]) -> Tuple[List[List[Any]], List[str]]:
    """提取消息里的更新块，返回 (patch 数组列表, warnings)。"""
    if not text:
        return [], []
    warnings: List[str] = []
    blocks = list(_UPDATE_RE.finditer(text))
    if len(blocks) > MAX_UPDATE_BLOCKS:
        warnings.append("更新块超过 %d 个，其余已忽略" % MAX_UPDATE_BLOCKS)
        blocks = blocks[:MAX_UPDATE_BLOCKS]
    out: List[List[Any]] = []
    for idx, match in enumerate(blocks):
        body = match.group(1)
        if len(body) > MAX_UPDATE_BLOCK:
            warnings.append("更新块 #%d 过大，已忽略" % (idx + 1))
            continue
        inner = _PATCH_TAG_RE.search(body)
        raw = (inner.group(1) if inner else body).strip()
        raw = _strip_fence(raw).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            try:
                parsed = lenient_json_loads(raw)
            except Exception:
                warnings.append("更新块 #%d 不是合法 JSON，已忽略" % (idx + 1))
                continue
        if not isinstance(parsed, list):
            warnings.append("更新块 #%d 不是 JSON Patch 数组，已忽略" % (idx + 1))
            continue
        out.append(parsed)
    return out, warnings


def apply_updates(
    stat_data: Optional[Dict[str, Any]],
    text: Optional[str],
) -> Tuple[Dict[str, Any], int, List[str]]:
    """逐块应用更新；每块独立原子。返回 (新状态, 成功块数, warnings)。"""
    patches, warnings = extract_patches(text)
    current = copy.deepcopy(stat_data) if isinstance(stat_data, dict) else {}
    applied = 0
    for idx, ops in enumerate(patches):
        try:
            current = apply_patch(current, ops)
            applied += 1
        except PatchError as exc:
            warnings.append("更新块 #%d 应用失败，已整块丢弃：%s" % (idx + 1, exc))
    return current, applied, warnings


def strip_update_blocks(text: Optional[str]) -> Optional[str]:
    """从 prompt 副本中剥离更新块与内联初值（存储原文保持不变）。"""
    if not text:
        return text
    return _INLINE_INIT_RE.sub("", _UPDATE_RE.sub("", text))


def last_state(messages: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """从可见消息回溯最后一处 stat_data 快照（重新编辑/重生成后重算）。"""
    for msg in reversed(list(messages or [])):
        if getattr(msg, "role", "") != "assistant":
            continue
        if getattr(msg, "hidden", False):
            continue
        state = getattr(msg, "stat_data", None)
        if isinstance(state, dict):
            return copy.deepcopy(state)
    return None


# ---- 会话级接线 ----


def mvu_active(character: Any, user_settings: Any, cfg: Any) -> bool:
    """该卡在本会话是否启用原生 MVU：卡声明 + 服务端总开关 + 用户开关。"""
    if character is None or not card_uses_mvu(character):
        return False
    if cfg is not None:
        mvu_cfg = getattr(cfg, "mvu", None)
        if mvu_cfg is not None and not bool(getattr(mvu_cfg, "enabled", True)):
            return False
    value = getattr(user_settings, "mvu_enabled", None)
    return True if value is None else bool(value)


def _macro_key_target(key: Any, char_name: str, user_name: str,
                      group_names: Optional[Sequence[str]]) -> Optional[str]:
    """把字面量 ``{{user}}`` 一类的键换成提示词侧同名值；非宏键返回 None。"""
    if not isinstance(key, str):
        return None
    match = _INIT_MACRO_KEY_RE.match(key.strip())
    if match is None:
        return None
    target = _macro_value(match.group(1).lower(), char_name, user_name,
                          group_names)
    return target or None


def _merge_missing(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    """只补 base 缺失的键，已有值（通常更新过）优先保留。"""
    for key, value in (extra or {}).items():
        if key not in base:
            base[key] = copy.deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _merge_missing(base[key], value)


def _rename_macro_keys(value: Any, char_name: str, user_name: str,
                       group_names: Optional[Sequence[str]]) -> bool:
    """递归展开状态里的宏键与宏文本（InitVar 旧数据迁移用）。返回是否改动。"""
    changed = False
    if isinstance(value, dict):
        for key in list(value.keys()):
            target = _macro_key_target(key, char_name, user_name, group_names)
            if target and target != key:
                item = value.pop(key)
                if target in value:
                    if isinstance(value[target], dict) and isinstance(item, dict):
                        _merge_missing(value[target], item)
                else:
                    value[target] = item
                key = target
                changed = True
            child = value.get(key)
            if isinstance(child, str):
                expanded = _expand_init_macros(
                    child, char_name=char_name, user_name=user_name,
                    group_names=group_names,
                )
                if expanded != child:
                    value[key] = expanded
                    changed = True
            elif isinstance(child, (dict, list)):
                if _rename_macro_keys(child, char_name, user_name,
                                      group_names):
                    changed = True
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                expanded = _expand_init_macros(
                    item, char_name=char_name, user_name=user_name,
                    group_names=group_names,
                )
                if expanded != item:
                    value[idx] = expanded
                    changed = True
            elif _rename_macro_keys(item, char_name, user_name, group_names):
                changed = True
    return changed


def _replay_updates_from_initial(session: Any, state: Dict[str, Any],
                                 char_name: str, user_name: str,
                                 group_names: Optional[Sequence[str]]) -> None:
    """从 InitVar 初值顺序重放可见消息的更新块，修复旧版本漏掉的更新。

    只在宏键迁移时调用（一次性）；正常加载仍以消息快照为准。重放比“信任
    最后一个快照”更能恢复此前因路径不匹配被整块丢弃的合法改动。
    """
    base = copy.deepcopy(state.get("initial") or {})
    carrier_id = str(state.get("char_id") or "")
    for msg in snapshot_messages(session):
        if getattr(msg, "role", "") != "assistant":
            continue
        if getattr(msg, "hidden", False) or getattr(msg, "tombstone", None):
            continue
        content = getattr(msg, "content", "") or ""
        if not content:
            continue
        mid = str(getattr(msg, "character_id", "") or "")
        if mid and carrier_id and mid != carrier_id:
            continue
        patches, _warnings = extract_patches(content)
        if not patches:
            continue
        base, _applied, _warns = apply_updates(base, content)
        msg.stat_data = copy.deepcopy(base)
    state["stat_data"] = base


def migrate_macro_keys(session: Any, char_name: str = "", user_name: str = "",
                       group_names: Optional[Sequence[str]] = None,
                       replay: bool = True) -> bool:
    """迁移旧会话里未展开的 InitVar 宏键，并重放更新块。返回是否改动。

    旧实现直接解析原始 YAML，``{{user}}`` 会作为字面量键留在 ``stat_data``；
    而提示词侧已展开成 ``"用户"``/用户角色名，模型据此生成的 patch 全部
    落空。迁移改名后按消息顺序重放，恢复此前被丢弃的更新。
    """
    state = getattr(session, "mvu", None)
    if not isinstance(state, dict) or not state.get("enabled"):
        return False
    # 只有“状态仍等于初值”时才敢从 messages 重放：若 stat_data 已有
    # 成功更新或用户手工写回，重放会把这些非消息来源的改动覆盖掉。
    pristine = (copy.deepcopy(state.get("stat_data") or {})
                == copy.deepcopy(state.get("initial") or {}))
    changed = False
    for field in ("initial", "stat_data"):
        value = state.get(field)
        if isinstance(value, dict):
            if _rename_macro_keys(value, char_name, user_name, group_names):
                changed = True
    for msg in snapshot_messages(session):
        snapshot = getattr(msg, "stat_data", None)
        if isinstance(snapshot, dict):
            if _rename_macro_keys(snapshot, char_name, user_name, group_names):
                changed = True
    if not changed:
        return False
    if replay and pristine:
        _replay_updates_from_initial(session, state, char_name, user_name,
                                     group_names)
    session.mvu = state
    return True


def ensure_session_state(session: Any, character: Any = None, *,
                         user_name: str = "",
                         group_names: Optional[Sequence[str]] = None,
                         ) -> bool:
    """惰性初始化/换卡重建会话 MVU 状态。返回是否改动（调用方据此落盘）。

    - 载体卡不可解析（被删除或重新导入换了 id）：**保留**已有状态，但会
      迁移未展开的 InitVar 宏键。打开会话时若在这里清空，会把历史
      ``stat_data`` 永久抹掉；
    - 载体卡明确不使用 MVU：清空 ``session.mvu``（非空时才返回 True）；
    - 已初始化且角色一致：只做宏键迁移，通常无操作；
    - 否则按 InitVar 重建，``stat_data`` 回到初值（换卡后旧状态不沿用）。
    """
    state = getattr(session, "mvu", None)
    if not isinstance(state, dict):
        state = {}
    if character is None:
        return migrate_macro_keys(session, user_name=user_name,
                                  group_names=group_names)
    if not card_uses_mvu(character):
        if state:
            session.mvu = {}
            return True
        return False
    char_name = str(getattr(character, "name", "") or "")
    char_id = str(getattr(character, "id", "") or "")
    if state.get("initialized") and state.get("char_id") == char_id:
        return migrate_macro_keys(session, char_name=char_name,
                                  user_name=user_name,
                                  group_names=group_names)
    new_state, _warnings = build_initial_state(
        character, user_name=user_name, group_names=group_names,
        first_mes=selected_greeting(
            character, getattr(session, "greeting_index", 0)
        ),
    )
    session.mvu = new_state
    return True


def carrier_character(session: Any, lore: Any) -> Any:
    """会话 MVU 载体卡：首个声明 MVU 的 AI 座位，回退到会话绑定角色。

    与 routes_chat 群聊路径的选取规则一致（会话级状态只跟一张卡走）。
    """
    from ..sessions.resolve import effective_character, member_character

    for m in getattr(session, "members", None) or []:
        if getattr(m, "is_user", False):
            continue
        cand = member_character(m, lore)
        if cand is not None and card_uses_mvu(cand):
            return cand
    return effective_character(session, lore)


def snapshot_messages(session: Any) -> List[Any]:
    """重算用消息序列：优先主私聊窗口，否则合并顶层与全部窗口。"""
    from ..sessions.resolve import primary_thread

    pt = primary_thread(session)
    if pt is not None and pt.messages:
        return list(pt.messages)
    msgs = list(getattr(session, "messages", None) or [])
    for t in getattr(session, "threads", None) or []:
        if t is not pt:
            msgs.extend(getattr(t, "messages", None) or [])
    return msgs


def recover_orphan_state(session: Any) -> bool:
    """载体卡已不可解析时，从消息快照恢复被旧版本清空的会话状态。

    旧实现会在打开「引用了已删除角色卡」的会话时把 ``session.mvu`` 清成
    ``{}``；消息快照仍在，可据此重建最小状态，让面板重新显示最新数值。
    仅在状态未启用且确有快照时生效，避免误复活普通会话。
    """
    state = getattr(session, "mvu", None)
    if isinstance(state, dict) and state.get("enabled"):
        return False
    latest = last_state(snapshot_messages(session))
    if latest is None:
        return False
    session.mvu = {
        "enabled": True,
        "initialized": True,
        "char_id": str(getattr(session, "character_id", "") or ""),
        "initial": {},
        "stat_data": latest,
    }
    return True


def session_user_name(session: Any, lore: Any) -> str:
    """用户角色名；与提示词侧一致（无用户卡时返回空串，宏回退成 "用户"）。"""
    try:
        from ..sessions.resolve import member_character, user_seat

        user_char = member_character(user_seat(session), lore)
        return str(getattr(user_char, "name", "") or "")
    except Exception:
        return ""


def sync_session_state(session: Any, lore: Any) -> bool:
    """会话加载时的惰性初始化 + 快照重算。返回是否改动（调用方据此落盘）。"""
    character = carrier_character(session, lore)
    user_name = session_user_name(session, lore)
    changed = ensure_session_state(session, character, user_name=user_name)
    if character is None and recover_orphan_state(session):
        # 孤儿状态来自消息快照，initial 为空：只改名、不从空初值重放
        migrate_macro_keys(session, user_name=user_name, replay=False)
        changed = True
    if recompute_state(session, snapshot_messages(session)):
        changed = True
    return changed


def recompute_state(session: Any, messages: Sequence[Any]) -> bool:
    """按可见消息的最后快照重算当前状态（编辑/重生成/删除/切换变体后）。

    无任何快照时回到 InitVar 初值。返回是否改动。
    """
    state = getattr(session, "mvu", None)
    if not isinstance(state, dict) or not state.get("enabled"):
        return False
    latest = last_state(messages)
    if latest is None:
        latest = copy.deepcopy(state.get("initial") or {})
    if latest == (state.get("stat_data") or {}):
        return False
    state["stat_data"] = latest
    session.mvu = state
    return True
