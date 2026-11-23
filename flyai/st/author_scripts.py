"""酒馆助手（TavernHelper / JS-Slash-Runner）作者脚本下发。

角色卡的 ``extensions.tavern_helper.scripts`` 是一棵脚本树：叶子节点带
``content``，目录节点带 ``scripts``。这里只做**数据抽取与限额**，服务端
绝不执行任何卡内 JavaScript。

安全边界：

- 只有用户显式开启 ``st_author_scripts_enabled``（管理员默认开）才会下发；
- 只下发启用中的脚本，单条/总量都有限额，超限截断并标记；
- 脚本内容原样返回给前端，前端在独立 iframe 中执行，不接触服务端凭据。

前端执行器在 ``webapp/src/services/st/authorRuntime.ts``。
"""

from __future__ import annotations

from typing import Any, Dict, List

MAX_SCRIPTS = 32
# 默认 5MB：整页面板脚本（约会大作战的全息终端）单文件约 4.3MB。
# 管理面板可按 1–5MB 调整，最终以 lore.max_author_script_mb 为准。
MAX_SCRIPT_CHARS = 5 * 1024 * 1024
MAX_TOTAL_CHARS = 5 * 1024 * 1024
MAX_DEPTH = 8
MAX_NAME = 120
MAX_ID = 120


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _script_tree(extensions: Any) -> List[Any]:
    if not isinstance(extensions, dict):
        return []
    helper = extensions.get("tavern_helper")
    if helper is None:
        helper = extensions.get("TavernHelper")
    if not isinstance(helper, dict):
        return []
    scripts = helper.get("scripts")
    return scripts if isinstance(scripts, list) else []


def _walk(nodes: List[Any], depth: int = 0) -> List[Dict[str, Any]]:
    """把脚本树拍平成叶子列表；目录节点递归。"""
    out: List[Dict[str, Any]] = []
    if depth > MAX_DEPTH:
        return out
    for node in nodes:
        if not isinstance(node, dict):
            continue
        children = node.get("scripts")
        kind = _text(node.get("type")).lower()
        if kind == "folder" or (isinstance(children, list) and children):
            out.extend(_walk(children if isinstance(children, list) else [], depth + 1))
            continue
        content = node.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        out.append(node)
    return out


def collect_author_scripts(
    character: Any,
    max_script_chars: int = MAX_SCRIPT_CHARS,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> Dict[str, Any]:
    """返回 ``{"scripts": [...], "truncated": bool}``。

    每个脚本保留前端执行所需的最小字段；``enabled`` 缺省按启用处理，
    显式 ``false`` 的脚本不下发。限额可由管理面板配置（1–5MB）。
    """
    if character is None:
        return {"scripts": [], "truncated": False}
    max_script_chars = max(1, int(max_script_chars))
    max_total_chars = max(1, int(max_total_chars))
    extensions = getattr(character, "extensions", None)
    leaves = _walk(_script_tree(extensions))
    scripts: List[Dict[str, Any]] = []
    total = 0
    truncated = False
    for index, node in enumerate(leaves):
        if len(scripts) >= MAX_SCRIPTS:
            truncated = True
            break
        if node.get("enabled") is False:
            continue
        content = node.get("content")
        if not isinstance(content, str):
            continue
        if len(content) > max_script_chars:
            truncated = True
            continue
        if total + len(content) > max_total_chars:
            truncated = True
            break
        total += len(content)
        scripts.append({
            "id": _text(node.get("id"))[:MAX_ID] or "script-%d" % (index + 1),
            "name": _text(node.get("name"))[:MAX_NAME] or "脚本 %d" % (index + 1),
            "content": content,
        })
    return {"scripts": scripts, "truncated": truncated}


def has_author_scripts(character: Any) -> bool:
    return bool(collect_author_scripts(character)["scripts"])
