"""会话自包含迁移包（.zip）：对话 JSON + 其中引用到的全部上传文件。

跨账号 / 跨服务器迁移时不再丢失立绘、背景、BGM 与已合成语音。包结构::

    conversation.json   # flyai-conversation v3，文本中保留导出端原始 URL
    manifest.json       # {"uid": ..., "files": [{"url", "zip"}]}
    files/<相对路径>     # 与导出端 upload_dir 内相对结构一致

相对路径形如 ``galgame/backgrounds/<sid>/ga_x.png``；导入时整体释放到
新账号 upload_dir 下，并把对话文本中的旧 URL 前缀改写为新账号。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

CONVERSATION_NAME = "conversation.json"
MANIFEST_NAME = "manifest.json"
FILES_PREFIX = "files/"

# 防护上限（正常迁移包远小于此）
MAX_ENTRIES = 1000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024

_URL_TAIL_STRIP = ".,;:!?()[]{}，。；：！？、）】》」』"


class TransferError(ValueError):
    """迁移包结构非法 / 超出安全限制。"""


def _url_prefix(uid: str) -> str:
    return "/uploads/%s/" % uid


def _safe_rel(rel: str) -> str:
    """校验相对路径：POSIX 风格、无盘符/反斜杠/穿越/空段。"""
    if not rel or rel.startswith("/") or "\\" in rel or ":" in rel:
        raise TransferError("包内文件路径非法: %r" % rel)
    parts = rel.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise TransferError("包内文件路径非法: %r" % rel)
    if "/".join(parts) != rel:
        raise TransferError("包内文件路径非规范: %r" % rel)
    return rel


def _collect_urls(value: Any, uid: str) -> List[str]:
    """递归收集 JSON 中所有字符串里出现的本账号上传 URL（去重、排序）。"""
    found = set()
    pat = re.compile(re.escape(_url_prefix(uid)) + r"""[^\s"\\<>）)]+""")

    def walk(v):
        if isinstance(v, str):
            for m in pat.findall(v):
                found.add(m.rstrip(_URL_TAIL_STRIP))
        elif isinstance(v, dict):
            for vv in v.values():
                walk(vv)
        elif isinstance(v, list):
            for vv in v:
                walk(vv)

    walk(value)
    return sorted(found)


def build_export_zip(payload: Dict, uid: str, upload_root: Path) -> bytes:
    """打包导出信封（含 data 的完整 dict）为 zip 字节。

    磁盘上已不存在的文件跳过（URL 仍留在文本中，导入端按悬空链接处理，
    与当前手工迁移的行为一致）。
    """
    upload_root = Path(upload_root)
    base = _url_prefix(uid)
    manifest_files = []
    file_items: List[Tuple[str, bytes]] = []

    for url in _collect_urls(payload, uid):
        rel = _safe_rel(url[len(base):])
        src = upload_root / rel
        # 双保险：解析结果必须仍在 upload_root 之内
        try:
            src.resolve().relative_to(upload_root.resolve())
        except (OSError, ValueError):
            raise TransferError("素材路径越界: %r" % rel)
        if not src.is_file():
            continue
        data = src.read_bytes()
        zip_name = FILES_PREFIX + rel
        file_items.append((zip_name, data))
        manifest_files.append({"url": url, "zip": zip_name})

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    manifest = {"uid": uid, "files": manifest_files}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        for zip_name, data in file_items:
            zf.writestr(zip_name, data)
        # 对话文本放最后写入无意义，保持清单顺序：文件写完后写主 JSON
        zf.writestr(CONVERSATION_NAME, text)
    return buf.getvalue()


def open_export_zip(raw: bytes) -> Tuple[str, List[Dict]]:
    """解析迁移包：返回 (conversation.json 文本, 条目列表)。

    条目为 ``{rel, url, data}``；不在此函数落盘，由调用方改写 URL 后
    经 :func:`materialize_files` 释放。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise TransferError("不是有效的 zip 文件") from exc

    with zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if len(infos) > MAX_ENTRIES or total > MAX_TOTAL_BYTES:
            raise TransferError("迁移包超出大小 / 文件数限制")

        if CONVERSATION_NAME not in zf.namelist():
            raise TransferError("包内缺少 %s" % CONVERSATION_NAME)
        text = zf.read(CONVERSATION_NAME).decode("utf-8")

        manifest = {}
        if MANIFEST_NAME in zf.namelist():
            try:
                manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except ValueError as exc:
                raise TransferError("manifest.json 解析失败") from exc

        entries = []
        seen = set()
        for item in manifest.get("files", []) or []:
            if not isinstance(item, dict):
                raise TransferError("manifest 文件条目非法")
            zip_name = str(item.get("zip") or "")
            url = str(item.get("url") or "")
            if not zip_name.startswith(FILES_PREFIX) or not url:
                raise TransferError("manifest 文件条目非法")
            rel = _safe_rel(zip_name[len(FILES_PREFIX):])
            info = zf.getinfo(zip_name)
            if info.file_size > MAX_FILE_BYTES:
                raise TransferError("单个文件超出限制: %s" % rel)
            data = zf.read(zip_name)
            if rel in seen:
                raise TransferError("包内路径重复: %s" % rel)
            seen.add(rel)
            entries.append({"rel": rel, "url": url, "data": data})

    return text, entries


def rewrite_urls(text: str, entries: List[Dict], new_uid: str) -> str:
    """把对话文本中的导出端 URL 全部替换为新账号 URL。"""
    for e in entries:
        new_url = _url_prefix(new_uid) + e["rel"]
        text = text.replace(e["url"], new_url)
    return text


def materialize_files(entries: List[Dict], upload_root: Path) -> None:
    """把条目释放到新账号上传目录（zip-slip 在 _safe_rel 之外再收口一次）。"""
    upload_root = Path(upload_root)
    root_resolved = upload_root.resolve()
    for e in entries:
        target = upload_root / e["rel"]
        try:
            target.resolve().relative_to(root_resolved)
        except (OSError, ValueError) as exc:
            raise TransferError("释放路径越界: %r" % e["rel"]) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(e["data"])
