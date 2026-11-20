"""酒馆（SillyTavern）OpenAI 预设 → FlyAI Preset。

预设 JSON 的关键字段：
- 顶层采样参数（temperature/top_p/top_k/top_a/min_p/惩罚/seed/…）；
- openai_max_tokens（回复上限，缺省回退 genamt）；
- prompts：prompt manager 块列表，每块有 enabled / injection_position
  （0=in-chat@depth，1=主提示词，null=替换内建标记）/ injection_depth /
  injection_order / role / identifier / content；
- assistant_prefill：assistant 强制前缀。

null 位置的内建标记：main/nsfw/enhanceDefinitions 视为主提示词块；
jailbreak 且 role=assistant → assistant_prefill；其余有内容的标记落到
tail（历史后 system，类似 PHI）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from ..st.regex_scripts import sanitize_regex_scripts
from ..tavern import lenient_json_loads
from .models import Preset, PresetBlock, sanitize_params

_ROLE_MAP = {0: "system", 1: "user", 2: "assistant"}
# 视为「主提示词」位置的内建标记
_MAIN_MARKERS = {"main", "nsfw", "enhanceDefinitions"}

ENVELOPE_TYPE = "flyai-preset"
ENVELOPE_VERSION = 1

# 酒馆「预设绑定正则」插件把脚本 JSON 存在该 identifier 的 prompt 块里
_REGEX_BINDINGS_ID = "regexes-bindings"
# 绑定数据可能包在这些键里（兼容不同版本的导出）
_REGEX_BINDING_KEYS = (
    "regex_scripts", "regexScripts", "scripts", "regexes", "bindings", "items",
)


def _decode_binding_content(content: Any) -> Any:
    """把 binding 块 content 解成对象；兼容双重 JSON 编码与脏 JSON。"""
    if isinstance(content, (list, dict)):
        return content
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()
    for _attempt in range(2):
        try:
            data = json.loads(text, strict=False)
        except (TypeError, ValueError):
            try:
                data = lenient_json_loads(text)
            except (ValueError, TypeError):
                return None
        # 导出工具偶尔会把 JSON 再编码成字符串
        if isinstance(data, str) and data.strip()[:1] in ("{", "["):
            text = data.strip()
            continue
        return data
    return None


def _extract_binding_scripts(obj: Any) -> list:
    """从已解码对象提取脚本候选：数组 / 包装键 / id→脚本映射 / 单条脚本。"""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    if obj.get("findRegex") or obj.get("scriptName"):
        return [obj]
    for key in _REGEX_BINDING_KEYS:
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    values = [v for v in obj.values() if isinstance(v, dict)]
    if any(v.get("findRegex") for v in values):
        return values
    return []


def _parse_regex_bindings(content: Any) -> Tuple[list, bool]:
    """解析 regexes-bindings 块 → (清洗后脚本列表, 是否解析成功)。"""
    if content is None or (isinstance(content, str) and not content.strip()):
        return [], True
    data = _decode_binding_content(content)
    if data is None:
        return [], False
    return sanitize_regex_scripts(_extract_binding_scripts(data)), True


def _dedupe_scripts(scripts: list) -> list:
    """按完整内容去重，保留首次出现的顺序。

    SPreset 等插件会把同一批正则同时写进 extensions.regex_scripts 和
    regexes-bindings 块；两边都读时不希望脚本被执行两遍。
    """
    seen = set()
    out = []
    for script in scripts:
        key = json.dumps(script, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(script)
    return out


class PresetImportError(ValueError):
    """预设格式无法识别。"""


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _block_from_prompt(raw: Dict[str, Any]) -> Tuple[PresetBlock, str]:
    """酒馆 prompt 项 → (PresetBlock, kind)；kind 为 main/depth/tail/prefill/skip。"""
    pos = raw.get("injection_position")
    st_role = _as_int(raw.get("role"), 0)
    role = _ROLE_MAP.get(st_role, "system")
    order = _as_int(raw.get("injection_order"), 100)
    identifier = str(raw.get("identifier") or "")

    b = PresetBlock(
        name=str(raw.get("name") or identifier or "未命名块"),
        content=raw.get("content") or "",
        enabled=bool(raw.get("enabled", True)),
        role=role,
        depth=max(0, _as_int(raw.get("injection_depth"), 4)),
        insert_order=order,
        identifier=identifier,
    )
    if pos == 0:
        b.position = "depth"
        return b, "depth"
    if pos == 1:
        b.position = "main"
        return b, "main"
    # injection_position=null：替换内建标记
    if identifier == "jailbreak":
        if role == "assistant":
            b.position = "tail"  # 实际走 assistant_prefill 槽
            return b, "prefill"
        b.position = "tail"
        return b, "tail"
    if identifier in _MAIN_MARKERS:
        b.position = "main"
        return b, "main"
    b.position = "tail"
    return b, "tail"


def preset_from_dict(data: Dict[str, Any], name: str = "") -> Tuple[Preset, Dict[str, int]]:
    if not isinstance(data, dict):
        raise PresetImportError("顶层不是 JSON 对象")
    # FlyAI 导出信封：直接还原
    if data.get("type") == ENVELOPE_TYPE:
        inner = data.get("data")
        if not isinstance(inner, dict):
            raise PresetImportError("信封缺少 data")
        p = Preset.from_dict(inner)
        if name:
            p.name = name
        p.id = ""  # 导入即新 id
        return p, {
            "blocks": len(p.blocks),
            "enabled": sum(1 for b in p.blocks if b.enabled),
            "regex_scripts": len(p.regex_scripts),
            "regex_invalid": 0,
        }

    params = sanitize_params(data)
    max_tokens = _as_int(data.get("openai_max_tokens"), 0)
    if max_tokens <= 0:
        max_tokens = _as_int(data.get("genamt"), 0)
    max_tokens = max(0, max_tokens)

    blocks = []
    stats = {"blocks": 0, "enabled": 0, "main": 0, "depth": 0, "tail": 0,
             "skipped": 0, "prefill": 0, "regex_scripts": 0, "regex_invalid": 0}
    prefill = ""
    binding_scripts: list = []
    prompts = data.get("prompts")
    nested = data.get("chatCompletionSettings")
    if not isinstance(prompts, list):
        # 部分导出把完整设置收在 chatCompletionSettings 里
        if isinstance(nested, dict) and isinstance(nested.get("prompts"), list):
            prompts = nested["prompts"]
    if not isinstance(prompts, list):
        raise PresetImportError("缺少 prompts 列表，不是酒馆 OpenAI 预设")
    for raw in prompts:
        if not isinstance(raw, dict):
            continue
        identifier = str(raw.get("identifier") or "").strip().lower().replace("_", "-")
        if identifier == _REGEX_BINDINGS_ID:
            scripts, ok = _parse_regex_bindings(raw.get("content"))
            if not ok:
                stats["regex_invalid"] += 1
            else:
                binding_scripts.extend(scripts)
            # 绑定块不是 prompt，绝不能注入上下文
            continue
        if not (raw.get("content") or "").strip():
            stats["skipped"] += 1
            continue
        block, kind = _block_from_prompt(raw)
        if kind == "prefill":
            if not prefill:
                prefill = block.content
                stats["prefill"] = 1
            # jailbreak 前缀块不再重复进 tail
            continue
        blocks.append(block)
        stats["blocks"] += 1
        if block.enabled:
            stats["enabled"] += 1
            stats[kind] += 1
    # 顶层 assistant_prefill（jailbreak 块优先）
    if not prefill and (data.get("assistant_prefill") or "").strip():
        prefill = str(data["assistant_prefill"]).strip()
        stats["prefill"] = 1
    # 酒馆新版把预设正则存在 extensions.regex_scripts（原样导出或收在
    # chatCompletionSettings 里）；旧版插件写 regexes-bindings 块；再兜底
    # 顶层 regex_scripts（非插件格式）。extensions 是权威来源，排在最前。
    raw_sources: list = []
    for doc in (data, nested):
        if not isinstance(doc, dict):
            continue
        ext = doc.get("extensions")
        if isinstance(ext, dict) and ext.get("regex_scripts") is not None:
            raw_sources.append(ext.get("regex_scripts"))
    raw_sources.extend(binding_scripts)
    if data.get("regex_scripts") is not None:
        raw_sources.append(data.get("regex_scripts"))
    elif isinstance(nested, dict) and nested.get("regex_scripts") is not None:
        raw_sources.append(nested.get("regex_scripts"))
    regex_scripts: list = []
    for value in raw_sources:
        scripts, ok = _parse_regex_bindings(value)
        if not ok:
            stats["regex_invalid"] += 1
        else:
            regex_scripts.extend(scripts)
    regex_scripts = _dedupe_scripts(sanitize_regex_scripts(regex_scripts))
    stats["regex_scripts"] = len(regex_scripts)

    p = Preset(
        name=(name or str(data.get("name") or "")).strip() or "导入的预设",
        note=str(data.get("description") or ""),
        source="tavern",
        params=params,
        max_tokens=max_tokens,
        assistant_prefill=prefill,
        blocks=blocks,
        regex_scripts=regex_scripts,
    )
    return p, stats


def preset_from_text(text: str, name: str = "") -> Tuple[Preset, Dict[str, int]]:
    try:
        data = json.loads(text, strict=False)
    except (TypeError, ValueError):
        data = lenient_json_loads(text)
    return preset_from_dict(data, name=name)


def preset_to_envelope(p: Preset) -> Dict[str, Any]:
    return {"type": ENVELOPE_TYPE, "version": ENVELOPE_VERSION,
            "name": p.name, "data": p.to_dict()}
