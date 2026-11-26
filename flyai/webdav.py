"""WebDAV 云备份：零新增依赖（httpx + stdlib），可随 overlay 增量发版。

设计要点：

- 备份包是普通 zip：``manifest.json`` + ``config/config.json`` + ``data/...``；
- SQLite 库用 ``Connection.backup`` 做在线快照，避免直接拷 WAL 中的半截事务；
- 恢复不直接覆盖正在使用的数据库：先下载并暂存到 ``data/.restore``，
  下次启动时在打开任何存储之前应用；
- 只实现 WebDAV 需要的动词：PROPFIND / MKCOL / PUT / GET / DELETE。

备份包里可能含 API Key、用户数据与上传文件，面板上需要明确提示。
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

logger = logging.getLogger("flyai.webdav")

BACKUP_PREFIX = "flyai-backup-"
BACKUP_SUFFIX = ".zip"
MANIFEST_NAME = "manifest.json"
CONFIG_PREFIX = "config/"
DATA_PREFIX = "data/"
RESTORE_DIR = ".restore"

# 安全上限：正常备份远小于这些值
MAX_BACKUP_FILE_BYTES = 512 * 1024 * 1024
MAX_BACKUP_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_RESTORE_ENTRIES = 200_000

# 同一时刻只允许一个备份/恢复任务（手动点击可能重入）
_BACKUP_LOCK = threading.RLock()
_RESTORE_LOCK = threading.Lock()

_SKIP_DIRS = {RESTORE_DIR}
_SKIP_SUFFIXES = (".db-wal", ".db-shm", ".db-journal")
_SKIP_MARKERS = (".bak-", ".tmp")


class WebDavError(ValueError):
    """WebDAV 配置或远端返回错误（消息直接展示给管理员）。"""


# ---------------- 配置 ----------------

def normalize_remote_dir(value: str) -> str:
    """远端目录统一成无首尾斜杠的 POSIX 相对路径；拒绝穿越。"""
    text = (value or "").strip().replace("\\", "/").strip("/")
    if not text:
        return ""
    parts = []
    for seg in text.split("/"):
        if not seg or seg in (".", ".."):
            raise WebDavError("远端目录不能包含 . 或 ..")
        if any(ord(ch) < 32 for ch in seg):
            raise WebDavError("远端目录包含非法字符")
        parts.append(seg)
    return "/".join(parts)


def _norm_base(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise WebDavError("请填写 WebDAV 地址")
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WebDavError("WebDAV 地址需以 http:// 或 https:// 开头")
    return text.rstrip("/") + "/"


# ---------------- WebDAV 客户端 ----------------

class WebDavClient:
    """最小的同步 WebDAV 客户端；传入 client 可注入 MockTransport 做测试。"""

    def __init__(self, settings: Any, client: Optional[httpx.Client] = None) -> None:
        self.base = _norm_base(getattr(settings, "url", ""))
        self.timeout = max(5, int(getattr(settings, "timeout_seconds", 60) or 60))
        username = (getattr(settings, "username", "") or "").strip()
        password = getattr(settings, "password", "") or ""
        verify = bool(getattr(settings, "verify_tls", True))
        self._owns_client = client is None
        if client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                verify=verify,
                auth=httpx.BasicAuth(username, password) if username else None,
                follow_redirects=True,
                headers={"User-Agent": "FlyAI-WebDAV/1"},
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WebDavClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- URL / 请求 --

    def _url(self, rel: str, is_dir: bool = False) -> str:
        rel = (rel or "").strip("/")
        quoted = "/".join(quote(seg, safe="") for seg in rel.split("/") if seg)
        suffix = "/" if is_dir and quoted else ""
        return self.base + quoted + suffix

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last = exc
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
            except httpx.TimeoutException as exc:
                raise WebDavError("WebDAV 请求超时：%s" % (exc,))
            except httpx.HTTPError as exc:
                raise WebDavError("WebDAV 请求失败：%s" % (exc,))
        raise WebDavError("连接 WebDAV 失败：%s" % (last,))

    @staticmethod
    def _check(resp: httpx.Response, action: str) -> None:
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.text[:200]
            except Exception:
                pass
            raise WebDavError(
                "%s失败：HTTP %d%s"
                % (action, resp.status_code, (" " + detail) if detail else "")
            )

    # -- 目录 --

    def ensure_dir(self, rel: str) -> None:
        """逐级 MKCOL；已存在（405/301）视为成功。"""
        rel = normalize_remote_dir(rel)
        if not rel:
            return
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            resp = self._request("MKCOL", self._url(sub, True))
            if resp.status_code in (200, 201, 204, 301, 405):
                continue
            self._check(resp, "创建远端目录")

    def propfind(self, rel: str, depth: int = 1) -> Optional[List[Dict[str, Any]]]:
        """返回条目列表；目录不存在（404）返回 None。"""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:displayname/><d:resourcetype/><d:getcontentlength/>"
            "<d:getlastmodified/>"
            "</d:prop></d:propfind>"
        )
        resp = self._request(
            "PROPFIND",
            self._url(rel, True),
            content=body.encode("utf-8"),
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code not in (200, 207):
            self._check(resp, "读取远端目录")
        return _parse_multistatus(resp.content, self.base)

    def list_backups(self, remote_dir: str) -> List[Dict[str, Any]]:
        rel = normalize_remote_dir(remote_dir)
        entries = self.propfind(rel, 1)
        if not entries:
            return []
        out = []
        for item in entries:
            name = item.get("name") or ""
            if not name.startswith(BACKUP_PREFIX) or not name.endswith(BACKUP_SUFFIX):
                continue
            if item.get("is_dir"):
                continue
            out.append(
                {
                    "name": name,
                    "size": int(item.get("size") or 0),
                    "modified": item.get("modified") or "",
                }
            )
        out.sort(key=lambda x: x["name"], reverse=True)
        return out

    # -- 文件 --

    def put_file(self, rel: str, file_path: Path) -> None:
        rel = normalize_remote_dir(rel)
        with open(file_path, "rb") as fh:
            resp = self._request(
                "PUT",
                self._url(rel),
                content=fh,
                headers={
                    "Content-Type": "application/zip",
                    "Content-Length": str(os.path.getsize(file_path)),
                },
                timeout=httpx.Timeout(self.timeout, connect=15.0, write=None),
            )
        if resp.status_code == 404:
            # 父目录不存在：补齐后重试一次
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            self.ensure_dir(parent)
            with open(file_path, "rb") as fh:
                resp = self._request(
                    "PUT",
                    self._url(rel),
                    content=fh,
                    headers={
                        "Content-Type": "application/zip",
                        "Content-Length": str(os.path.getsize(file_path)),
                    },
                    timeout=httpx.Timeout(self.timeout, connect=15.0, write=None),
                )
        if resp.status_code not in (200, 201, 204):
            self._check(resp, "上传备份")

    def download_to(self, rel: str, dest: Path, max_bytes: int) -> int:
        rel = normalize_remote_dir(rel)
        total = 0
        try:
            with self._client.stream(
                "GET",
                self._url(rel),
                timeout=httpx.Timeout(self.timeout, connect=15.0, read=None),
            ) as resp:
                if resp.status_code >= 400:
                    raise WebDavError("下载备份失败：HTTP %d" % resp.status_code)
                with open(dest, "wb") as out:
                    for chunk in resp.iter_bytes(1 << 20):
                        total += len(chunk)
                        if total > max_bytes:
                            raise WebDavError("备份文件超过大小上限")
                        out.write(chunk)
        except httpx.HTTPError as exc:
            raise WebDavError("下载备份失败：%s" % (exc,))
        return total

    def delete(self, rel: str) -> None:
        resp = self._request("DELETE", self._url(normalize_remote_dir(rel)))
        if resp.status_code == 404:
            return
        if resp.status_code not in (200, 204):
            self._check(resp, "删除远端备份")

    def test(self) -> str:
        entries = self.propfind("", 0)
        if entries is None:
            raise WebDavError("WebDAV 目录不存在或无权访问")
        return "连接成功"


def _parse_multistatus(raw: bytes, base: str) -> List[Dict[str, Any]]:
    """解析 PROPFIND 响应；命名空间前缀不可信，按 local-name 取值。"""
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise WebDavError("WebDAV 返回了无法解析的 XML：%s" % exc)

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    out: List[Dict[str, Any]] = []
    base_path = unquote(urlsplit(base).path).rstrip("/")
    for node in root.iter():
        if local(node.tag) != "response":
            continue
        href = ""
        is_dir = False
        size = 0
        modified = ""
        for child in node.iter():
            name = local(child.tag)
            if name == "href" and not href:
                href = (child.text or "").strip()
            elif name == "collection":
                is_dir = True
            elif name == "getcontentlength" and child.text:
                try:
                    size = int(child.text.strip())
                except ValueError:
                    size = 0
            elif name == "getlastmodified" and child.text and not modified:
                modified = _parse_http_date(child.text.strip())
        if not href:
            continue
        path = unquote(urlsplit(href).path)
        clean = path.rstrip("/")
        if not clean or clean == base_path:
            continue
        name = clean.rsplit("/", 1)[-1]
        out.append(
            {
                "name": unquote(name),
                "is_dir": is_dir,
                "size": size,
                "modified": modified,
            }
        )
    return out


def _parse_http_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
        return dt.isoformat()
    except (TypeError, ValueError):
        return value


# ---------------- 备份包 ----------------

def _is_skipped(rel: Path) -> bool:
    parts = rel.parts
    if any(part in _SKIP_DIRS for part in parts):
        return True
    if any(marker in rel.name for marker in _SKIP_MARKERS):
        return True
    if rel.name.endswith(_SKIP_SUFFIXES):
        return True
    if rel.name.startswith("."):
        return True
    return False


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """用 SQLite 在线备份 API 生成一致快照（含 WAL 中已提交的数据）。"""
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _add_file(zf: zipfile.ZipFile, src: Path, arcname: str, tmpdir: Path) -> int:
    """把文件写入 zip；SQLite 先快照，其它文件直接流式写入。"""
    size = src.stat().st_size
    if src.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
        snap = Path(tmpdir) / ("%s.snapshot" % os.urandom(8).hex())
        try:
            _snapshot_sqlite(src, snap)
            zf.write(snap, arcname)
            return snap.stat().st_size
        except sqlite3.Error:
            logger.warning("SQLite 快照失败，改为直接复制：%s", src)
        finally:
            try:
                snap.unlink()
            except OSError:
                pass
    zf.write(src, arcname)
    return size


def create_backup_zip(
    config_path: Path,
    data_dir: Path,
    include_uploads: bool,
) -> Tuple[Path, Dict[str, Any]]:
    """生成备份 zip（临时文件），返回 (路径, manifest)。

    调用方负责删除临时文件。备份期间持有模块级锁，重复点击返回 409。
    """
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise WebDavError("已有备份任务在执行，请稍后再试")
    tmp_path = ""
    try:
        config_path = Path(config_path)
        data_dir = Path(data_dir)
        fd, tmp_path = tempfile.mkstemp(prefix="flyai-webdav-", suffix=".zip")
        os.close(fd)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        count = 0
        total = 0
        with tempfile.TemporaryDirectory(prefix="flyai-webdav-db-") as tmpdir:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                if config_path.is_file():
                    zf.write(config_path, CONFIG_PREFIX + "config.json")
                    count += 1
                if data_dir.is_dir():
                    for src in sorted(data_dir.rglob("*")):
                        if not src.is_file():
                            continue
                        rel = src.relative_to(data_dir)
                        if _is_skipped(rel):
                            continue
                        if not include_uploads and rel.parts and rel.parts[0] == "uploads":
                            continue
                        arcname = DATA_PREFIX + rel.as_posix()
                        size = _add_file(zf, src, arcname, Path(tmpdir))
                        total += size
                        count += 1
                        if total > MAX_BACKUP_TOTAL_BYTES:
                            raise WebDavError(
                                "备份内容超过 4 GiB，请关闭「包含上传文件」或清理数据后重试"
                            )
                manifest = {
                    "format": "flyai-backup",
                    "version": 1,
                    "created_at": created_at,
                    "include_uploads": bool(include_uploads),
                    "entries": count,
                    "bytes": total,
                }
                zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        return Path(tmp_path), manifest
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    finally:
        _BACKUP_LOCK.release()


def backup_name(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now()
    return "%s%s%s" % (BACKUP_PREFIX, moment.strftime("%Y%m%d-%H%M%S"), BACKUP_SUFFIX)


def upload_backup(
    client: WebDavClient,
    remote_dir: str,
    include_uploads: bool,
    config_path: Path,
    data_dir: Path,
    keep: int = 5,
) -> Dict[str, Any]:
    """创建并上传备份，成功后按 keep 清理最旧的远端备份。"""
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise WebDavError("已有备份任务在执行，请稍后再试")
    try:
        remote_dir = normalize_remote_dir(remote_dir)
        tmp, manifest = create_backup_zip(config_path, data_dir, include_uploads)
        try:
            client.ensure_dir(remote_dir)
            name = backup_name()
            # 同一秒内重复备份：追加序号避免覆盖
            existing = {item["name"] for item in client.list_backups(remote_dir)}
            if name in existing:
                name = name[:-4] + "-%d.zip" % int(time.time() % 1000)
            rel = (remote_dir + "/" + name) if remote_dir else name
            client.put_file(rel, tmp)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

        removed: List[str] = []
        if keep > 0:
            try:
                backups = client.list_backups(remote_dir)
                for old in backups[keep:]:
                    old_rel = (remote_dir + "/" + old["name"]) if remote_dir else old["name"]
                    try:
                        client.delete(old_rel)
                        removed.append(old["name"])
                    except WebDavError:
                        logger.warning("清理旧备份失败：%s", old["name"])
            except WebDavError:
                logger.warning("读取备份列表失败，跳过清理", exc_info=True)
        return {
            "name": name,
            "size": manifest.get("bytes", 0),
            "entries": manifest.get("entries", 0),
            "removed": removed,
        }
    finally:
        _BACKUP_LOCK.release()


# ---------------- 恢复到暂存区 ----------------

def _safe_zip_rel(name: str) -> Optional[str]:
    """校验 zip 内路径；只接受 manifest.json / config/ / data/。"""
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if name == MANIFEST_NAME:
        return name
    if name.startswith(CONFIG_PREFIX):
        return name
    if name.startswith(DATA_PREFIX):
        rel = name[len(DATA_PREFIX):]
        if not rel or rel.split("/")[0] in _SKIP_DIRS:
            return None
        return name
    return None


def stage_restore(zip_path: Path, data_dir: Path, source_name: str) -> Dict[str, Any]:
    """校验备份包并解压到 data/.restore/payload，等下次启动应用。"""
    if not _RESTORE_LOCK.acquire(blocking=False):
        raise WebDavError("已有恢复任务在执行，请稍后再试")
    restore_root = Path(data_dir) / RESTORE_DIR
    try:
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as exc:
            raise WebDavError("不是有效的备份包：%s" % exc)

        infos = zf.infolist()
        if len(infos) > MAX_RESTORE_ENTRIES:
            raise WebDavError("备份包条目过多，拒绝恢复")
        total = sum(info.file_size for info in infos)
        if total > MAX_BACKUP_TOTAL_BYTES:
            raise WebDavError("备份包解压后超过 4 GiB，拒绝恢复")

        manifest_raw = None
        for info in infos:
            if info.filename == MANIFEST_NAME:
                manifest_raw = zf.read(info)
                break
        if manifest_raw is None:
            raise WebDavError("备份包缺少 manifest.json")
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebDavError("备份包 manifest.json 无法解析")
        if manifest.get("format") != "flyai-backup":
            raise WebDavError("不是 FlyAI 生成的备份包")

        if restore_root.exists():
            shutil.rmtree(restore_root)
        payload = restore_root / "payload"
        payload.mkdir(parents=True, exist_ok=True)

        files = 0
        written = 0
        for info in infos:
            if info.is_dir():
                continue
            name = _safe_zip_rel(info.filename)
            if name is None:
                raise WebDavError("备份包包含非法路径：%s" % info.filename)
            if name == MANIFEST_NAME:
                continue
            dest = payload / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            file_bytes = 0
            with zf.open(info) as src, open(dest, "wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    written += len(chunk)
                    if file_bytes > MAX_BACKUP_FILE_BYTES:
                        raise WebDavError("备份包单个文件超过大小上限")
                    if written > MAX_BACKUP_TOTAL_BYTES:
                        raise WebDavError("备份包解压后超过大小上限")
                    out.write(chunk)
            files += 1

        pending = {
            "name": source_name,
            "staged_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "manifest": manifest,
            "files": files,
            "bytes": written,
        }
        (restore_root / "pending.json").write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return pending
    except Exception:
        try:
            if restore_root.exists():
                shutil.rmtree(restore_root)
        except OSError:
            pass
        raise
    finally:
        _RESTORE_LOCK.release()


def apply_pending_restore(config_path: Path, data_dir: Path) -> Optional[Dict[str, Any]]:
    """启动时应用暂存恢复；在任何存储打开之前调用。"""
    restore_root = Path(data_dir) / RESTORE_DIR
    pending_path = restore_root / "pending.json"
    payload = restore_root / "payload"
    if not pending_path.is_file() or not payload.is_dir():
        return None
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("恢复暂存清单损坏，已忽略")
        _cleanup_restore(restore_root)
        return None

    data_dir = Path(data_dir)
    applied = 0
    for src in payload.rglob("*"):
        if not src.is_file():
            continue
        arc = src.relative_to(payload).as_posix()
        if arc.startswith(CONFIG_PREFIX):
            dest = Path(config_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                try:
                    shutil.copy2(dest, dest.with_name(dest.name + ".pre-restore"))
                except OSError:
                    logger.warning("保留恢复前配置失败", exc_info=True)
        elif arc.startswith(DATA_PREFIX):
            rel = arc[len(DATA_PREFIX):]
            dest = data_dir / Path(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            for sidecar in (str(dest) + "-wal", str(dest) + "-shm"):
                try:
                    os.unlink(sidecar)
                except OSError:
                    pass
        else:
            continue
        os.replace(str(src), str(dest))
        applied += 1

    _cleanup_restore(restore_root)
    logger.warning(
        "已应用 WebDAV 恢复包 %s：覆盖 %d 个文件",
        pending.get("name", ""),
        applied,
    )
    pending["applied"] = applied
    return pending


def _cleanup_restore(restore_root: Path) -> None:
    try:
        shutil.rmtree(restore_root)
    except OSError:
        logger.warning("清理恢复暂存目录失败", exc_info=True)


# ---------------- 对外服务封装 ----------------

def settings_or_error(settings: Any) -> None:
    if not bool(getattr(settings, "enabled", False)):
        raise WebDavError("WebDAV 备份未启用")
    if not (getattr(settings, "url", "") or "").strip():
        raise WebDavError("请先填写 WebDAV 地址")


def make_client(settings: Any) -> WebDavClient:
    settings_or_error(settings)
    return WebDavClient(settings)
