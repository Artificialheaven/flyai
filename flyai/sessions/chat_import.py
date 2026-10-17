"""第三方聊天工具导出的对话历史导入。

当前支持 Chatbox（chatboxai）的两种导出物：
- 备份 JSON（设置 → 数据 → 备份）：顶层含 chats 列表，一次可含多个对话；
  也兼容单个对话对象 {messages: [...]} 与纯消息数组。
- 单对话 Markdown 导出：按 User/Assistant（含 emoji、中英文）标题切段。

输出统一为 flyai-conversation v2 导出包的 data 形态（私聊房间），交由
routes_session 的 _build_imported_session / _resolve_import_cards_world 重建，
不直接落库，以便与自有格式导入共享同一条自愈链路。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from ..schemas import ImageAttachment, Message


class ChatImportError(ValueError):
    """第三方对话文件无法识别或解析。"""


# Chatbox 角色词与内部角色的归一（developer 归入 system）
_ROLE_WORDS = {
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "bot": "assistant",
    "system": "system",
    "developer": "system",
    "tool": "tool",
}

# 备份 JSON 中可能承载对话列表的字段（不同版本/衍生版命名不一）
_CHAT_LIST_KEYS = (
    "chats", "conversations", "sessions", "histories",
    "conversation", "history", "items",
)

# Markdown 角色标题里的 emoji → 角色（先于文字判断）
_EMOJI_ROLE = {
    "🤖": "assistant",
    "🧑": "user",
    "👤": "user",
    "💁": "user",
    "🙋": "user",
    "🖥": "system",
    "💻": "system",
}


# ---------- 入口 ----------

def parse_external_chats(raw: bytes) -> List[Dict[str, Any]]:
    """解析第三方导出文件，返回若干个 flyai-conversation v2 data dict。

    JSON 返回多个（备份可含多对话），Markdown 返回一个。没有任何可导入
    消息时抛 ChatImportError。
    """
    text = _decode(raw)
    stripped = text.strip()
    if not stripped:
        raise ChatImportError("文件为空")

    if stripped[0] in "{[":
        try:
            obj = json.loads(stripped)
        except ValueError as exc:
            raise ChatImportError("JSON 解析失败: %s" % exc)
        # 自家导出包走错入口时给出明确提示，而不是「找不到对话」
        if isinstance(obj, dict) and obj.get("type") == "flyai-conversation":
            raise ChatImportError("这是 FlyAI 自家导出包，请使用普通「导入对话」入口")
        chats = _extract_chats(obj)
        if not chats:
            raise ChatImportError("未在 JSON 中找到对话记录（需要 chats/messages 列表）")
        datas: List[Optional[Dict[str, Any]]] = [
            _chat_to_data(c, i) for i, c in enumerate(chats)
        ]
    else:
        datas = [_markdown_to_data(stripped)]

    out = [d for d in datas if d is not None]
    if not out:
        raise ChatImportError("文件中没有可导入的对话消息")
    return out


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


# ---------- JSON ----------

def _looks_like_chat(x: Any) -> bool:
    return isinstance(x, dict) and isinstance(x.get("messages"), list)


def _extract_chats(obj: Any) -> List[Dict[str, Any]]:
    """从备份 JSON 中抽出对话对象列表。"""
    if isinstance(obj, list):
        # 纯消息数组：[{role, content}, ...] → 包成一个对话
        if obj and all(isinstance(x, dict) and "role" in x for x in obj):
            return [{"title": "", "messages": obj}]
        return [x for x in obj if _looks_like_chat(x)]
    if isinstance(obj, dict):
        if _looks_like_chat(obj):
            return [obj]
        for key in _CHAT_LIST_KEYS:
            v = obj.get(key)
            if isinstance(v, list):
                chats = [x for x in v if _looks_like_chat(x)]
                if chats:
                    return chats
    return []


def _chat_to_data(chat: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """单个 Chatbox 对话 → flyai v2 data dict；无有效消息返回 None。"""
    raw_msgs = chat.get("messages") or []
    messages: List[Dict[str, Any]] = []
    for m in raw_msgs:
        converted = _convert_json_message(m)
        if converted is not None:
            messages.append(converted)
    if not messages:
        return None

    title = _clean_title(chat.get("title") or chat.get("name")) \
        or "Chatbox 对话 %d" % (index + 1)

    created = _iso_ts(chat.get("createdAt") or chat.get("created_at"))
    updated = _iso_ts(chat.get("updatedAt") or chat.get("updated_at"))
    if not created:
        created = _iso_ts(_first_ts(raw_msgs))
    if not updated:
        updated = _iso_ts(_last_ts(raw_msgs))

    return _build_data(title, messages, created, updated)


def _convert_json_message(m: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(m, dict):
        return None
    role = _ROLE_WORDS.get(str(m.get("role") or "").strip().lower())
    if role is None:
        return None

    text, part_images = _split_content(m.get("content"))
    text = (text or "").strip()
    images = _collect_images(m.get("images")) + part_images

    # Chatbox 的深度思考字段（不同版本命名不一）
    thinking = m.get("reasoning") or m.get("reasoning_content") or m.get("thinking")
    if not isinstance(thinking, str):
        thinking = None

    if not text and not images and not thinking:
        return None

    msg = Message(
        role=role,
        content=text or None,
        thinking=thinking or None,
        images=images or None,
    )
    return msg.to_dict()


def _split_content(content: Any) -> Tuple[str, List[ImageAttachment]]:
    """content 兼容字符串与多模态 parts 数组两种形态。"""
    if isinstance(content, str):
        return content, []
    images: List[ImageAttachment] = []
    texts: List[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                texts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text", "output_text") and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif ptype in ("image", "image_url", "input_image", "image_content"):
                spec = part.get("image_url")
                if isinstance(spec, dict):
                    spec = spec.get("url")
                spec = spec or part.get("url") or part.get("data") or part.get("source")
                img = _to_image(spec)
                if img is not None:
                    images.append(img)
    return "\n".join(t for t in texts if t), images


def _collect_images(raw: Any) -> List[ImageAttachment]:
    """Chatbox 的 images 字段：dataURL 字符串或 {dataUrl} 对象。"""
    if not isinstance(raw, list):
        return []
    out: List[ImageAttachment] = []
    for item in raw:
        spec: Any = item
        if isinstance(item, dict):
            spec = (item.get("dataUrl") or item.get("data_url")
                    or item.get("url") or item.get("data")
                    or item.get("base64") or item.get("source"))
        img = _to_image(spec)
        if img is not None:
            out.append(img)
    return out


_DATA_URL_RE = re.compile(r"^data:([^;,]*)?(;base64)?,(.*)$", re.S)
_PLAIN_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def _to_image(spec: Any) -> Optional[ImageAttachment]:
    """把 dataURL / 纯 base64 / {media_type,data} 统一成 ImageAttachment。

    http(s) 链接与本地文件路径无法随包迁移，丢弃（消息文本仍保留）。
    """
    if isinstance(spec, dict):
        media = spec.get("media_type") or spec.get("mediaType") or spec.get("mime") or "image/jpeg"
        data = spec.get("data") or spec.get("base64") or ""
        if isinstance(data, str) and data:
            spec = data
        else:
            return None
    if not isinstance(spec, str) or not spec:
        return None
    if spec.startswith("http://") or spec.startswith("https://"):
        return None
    if spec.startswith("data:"):
        media, data = _strip_data_url(spec)
        return ImageAttachment(media_type=media or "image/jpeg", data=data)
    # 裸 base64：要求够长且字符合法，避免把普通文本误判成图片
    compact = "".join(spec.split())
    if len(compact) > 200 and _PLAIN_B64_RE.match(compact):
        try:
            base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            return None
        return ImageAttachment(media_type="image/jpeg", data=compact)
    return None


def _strip_data_url(s: str) -> Tuple[Optional[str], str]:
    m = _DATA_URL_RE.match(s)
    if not m:
        return None, ""
    media, is_b64, payload = m.group(1), m.group(2), m.group(3)
    if is_b64:
        return (media or "image/jpeg"), payload
    # 非 base64 的 data URL：URL 解码后重新编码
    return (media or "text/plain"), base64.b64encode(unquote(payload).encode("utf-8")).decode("ascii")


def _first_ts(raw_msgs: List[Any]) -> Any:
    for m in raw_msgs:
        if isinstance(m, dict) and (m.get("createdAt") or m.get("created_at")):
            return m.get("createdAt") or m.get("created_at")
    return None


def _last_ts(raw_msgs: List[Any]) -> Any:
    for m in reversed(raw_msgs):
        if isinstance(m, dict) and (m.get("updatedAt") or m.get("updated_at")
                                    or m.get("createdAt") or m.get("created_at")):
            return (m.get("updatedAt") or m.get("updated_at")
                    or m.get("createdAt") or m.get("created_at"))
    return None


def _iso_ts(v: Any) -> str:
    """毫秒/秒 epoch 或字符串 → 本地时区 ISO（与 JsonSessionStore 的口径一致）。"""
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, (int, float)) and v > 0:
        ts = float(v)
        if ts > 1e12:  # 毫秒
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return ""
    return ""


# ---------- Markdown ----------
#
# Chatbox 真实导出格式（show/基础.md）：
#   # 对话标题
#   ## 1. 话题标题（可多个，仅分段，不导入）
#   **user**:            ← 粗体角色标签 + 可选冒号
#                        ← 空行
#   ```                  ← 围栏代码块，正文在其中
#   消息正文
#   ```
# 同时兼容旧版/衍生工具的 ## User / **User** 裸文本（无围栏）写法。

_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BOLD_LABEL_RE = re.compile(r"^\*{1,2}(.+?)\*{1,2}\s*[：:]?\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\s`~]*$")


def _markdown_to_data(text: str) -> Optional[Dict[str, Any]]:
    title = ""
    h1_seen = False
    messages: List[Tuple[str, List[str]]] = []

    role: Optional[str] = None
    buf: List[str] = []
    # ""=正文尚未开始；"fenced"=Chatbox 围栏写法；"plain"=裸文本写法
    mode = ""
    fence_marker = ""
    in_fence = False

    def flush() -> None:
        if role is not None:
            messages.append((role, buf))

    for raw in text.splitlines():
        line = raw.strip()

        # 围栏内：一切原样作为正文（包括标签模样的行、空行、反引号缩进）
        if in_fence:
            if line.startswith(fence_marker):
                in_fence = False
            else:
                buf.append(raw)
            continue

        # 围栏开闭行（只在某条消息已开始后有效）
        fm = _FENCE_RE.match(line)
        if fm and role is not None and mode in ("", "fenced"):
            if mode == "":
                mode = "fenced"
                in_fence = True
                fence_marker = fm.group(1)[:3]
            continue

        if not line:
            if mode == "plain":
                buf.append("")
            continue

        # 标题行
        hm = _MD_HEAD_RE.match(line)
        if hm:
            level = len(hm.group(1))
            label = hm.group(2).strip()
            if level == 1 and not h1_seen:
                h1_seen = True
                if not title:
                    title = label
                continue
            new_role = _role_from_label(label)
            if new_role is not None:
                # ## User 这类角色标题：开启新消息（裸文本模式）
                flush()
                role, buf, mode = new_role, [], "plain"
            # 其它标题（## 1. 话题 等）：分段标记，丢弃，不收进正文
            continue

        # **role**: 标签行
        if len(line) <= 40:
            bm = _BOLD_LABEL_RE.match(line)
            if bm:
                new_role = _role_from_label(bm.group(1))
                if new_role is not None:
                    flush()
                    role, buf, mode = new_role, [], ""
                    continue

        # 普通文本行
        if role is not None:
            if mode == "fenced":
                # 围栏已关闭：之后到下一标签前的内容是导出签名（Chatbox HTML 页脚），丢弃
                continue
            mode = "plain"
            buf.append(raw)

    flush()

    out_msgs: List[Dict[str, Any]] = []
    for r, lines in messages:
        body = "\n".join(lines).strip()
        if body:
            out_msgs.append(Message(role=r, content=body).to_dict())
    if not out_msgs:
        raise ChatImportError(
            "Markdown 中未识别到消息段（期望 **user**/**assistant** 标签加代码围栏，"
            "或 ## User / ## Assistant 标题）")

    return _build_data(_clean_title(title) or "Chatbox 导入对话", out_msgs, "", "")


def _role_from_label(label: str) -> Optional[str]:
    for emoji, r in _EMOJI_ROLE.items():
        if emoji in label:
            return r
    # 去空格/标点后做英文小写包含；中文直接包含
    bare = re.sub(r"[\s*_`>:：\-（()）]+", "", label).lower()
    if "assistant" in bare or "chatbox" in bare:
        return "assistant"
    if "system" in bare:
        return "system"
    if "user" in bare:
        return "user"
    if "助手" in label or "助理" in label:
        return "assistant"
    if "系统" in label:
        return "system"
    if "用户" in label:
        return "user"
    return None


# ---------- 组装 v2 data ----------

def _clean_title(t: Any) -> str:
    if not isinstance(t, str):
        return ""
    t = t.strip()
    return t[:60] if t else ""


def _build_data(title: str, messages: List[Dict[str, Any]],
                created: str, updated: str) -> Dict[str, Any]:
    """组装一个单 AI 私聊房间：用户座位 + 一个无名 AI 座位 + dm 线程。"""
    user_m = {
        "id": uuid.uuid4().hex,
        "name": "你",
        "is_user": True,
        "character_id": "",
    }
    ai_m = {
        "id": uuid.uuid4().hex,
        "name": "AI",
        "is_user": False,
        "character_id": "",
    }
    thread = {
        "id": uuid.uuid4().hex,
        "kind": "dm",
        "title": "私聊",
        "member_ids": [ai_m["id"]],
        "messages": messages,
        "edits": {},
    }
    return {
        "session": {
            "title": title,
            "created_at": created,
            "updated_at": updated,
            "attributes": {},
        },
        "members": [user_m, ai_m],
        "threads": [thread],
        "messages": [],
        "characters": [],
        "character": None,
        "world": None,
    }
