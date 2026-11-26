"""WebDAV 云备份接口（仅管理员）。

- POST /api/webdav/test        测试连接
- POST /api/webdav/backup      立即备份并上传，按 keep 清理旧包
- GET  /api/webdav/backups     列出远端备份
- POST /api/webdav/restore     下载并暂存备份，重启后生效
- GET  /api/webdav/pending     查看待生效的恢复任务
- DELETE /api/webdav/backups/{name}  删除远端备份

实现只依赖 httpx 与标准库，保持 requirements.txt 不变，可随 overlay 发版。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import _env_config_path
from ..user import ADMIN_UID
from ..webdav import (
    MAX_BACKUP_TOTAL_BYTES,
    WebDavError,
    make_client,
    normalize_remote_dir,
    stage_restore,
    upload_backup,
)
from .routes_config import _uid

router = APIRouter(prefix="/api/webdav", tags=["webdav"])

_NAME_RE = re.compile(r"^flyai-backup-[A-Za-z0-9_.-]+\.zip$")


class RestorePayload(BaseModel):
    name: str


def _admin(request: Request):
    uid = _uid(request)
    if uid != ADMIN_UID:
        raise HTTPException(403, "WebDAV 备份仅管理员可用")
    return request.app.state.flyai


def _remote_dir(state) -> str:
    try:
        return normalize_remote_dir(state.config.webdav.remote_dir)
    except WebDavError as exc:
        raise HTTPException(400, str(exc))


def _client(state):
    try:
        return make_client(state.config.webdav)
    except WebDavError as exc:
        raise HTTPException(400, str(exc))


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or ".." in name:
        raise HTTPException(400, "备份文件名非法")
    return name


@router.post("/test")
def test_connection(request: Request) -> Dict[str, Any]:
    state = _admin(request)
    client = _client(state)
    try:
        message = client.test()
        return {"ok": True, "message": message}
    except WebDavError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        client.close()


@router.post("/backup")
def backup_now(request: Request) -> Dict[str, Any]:
    state = _admin(request)
    cfg = state.config.webdav
    client = _client(state)
    try:
        result = upload_backup(
            client,
            _remote_dir(state),
            bool(cfg.include_uploads),
            Path(state.config_path) if state.config_path else _env_config_path(),
            Path(state.data_dir),
            int(cfg.keep or 5),
        )
        return {"ok": True, **result}
    except WebDavError as exc:
        raise HTTPException(400, str(exc))
    finally:
        client.close()


@router.get("/backups")
def list_backups(request: Request) -> Dict[str, Any]:
    state = _admin(request)
    client = _client(state)
    try:
        remote_dir = _remote_dir(state)
        return {
            "remote_dir": remote_dir,
            "backups": client.list_backups(remote_dir),
        }
    except WebDavError as exc:
        raise HTTPException(400, str(exc))
    finally:
        client.close()


@router.post("/restore")
def restore_backup(payload: RestorePayload, request: Request) -> Dict[str, Any]:
    state = _admin(request)
    name = _check_name(payload.name)
    client = _client(state)
    remote_dir = _remote_dir(state)
    rel = (remote_dir + "/" + name) if remote_dir else name
    fd, tmp = tempfile.mkstemp(prefix="flyai-restore-", suffix=".zip")
    os.close(fd)
    try:
        client.download_to(rel, Path(tmp), MAX_BACKUP_TOTAL_BYTES)
        result = stage_restore(Path(tmp), Path(state.data_dir), name)
        return {
            "ok": True,
            "name": name,
            "files": result.get("files", 0),
            "bytes": result.get("bytes", 0),
            "restart_required": True,
        }
    except WebDavError as exc:
        raise HTTPException(400, str(exc))
    finally:
        client.close()
        try:
            Path(tmp).unlink()
        except OSError:
            pass


@router.get("/pending")
def pending_restore(request: Request) -> Dict[str, Any]:
    state = _admin(request)
    path = Path(state.data_dir) / ".restore" / "pending.json"
    if not path.is_file():
        return {"pending": None}
    import json

    try:
        return {"pending": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"pending": None}


@router.delete("/backups/{name}")
def delete_backup(name: str, request: Request) -> Dict[str, Any]:
    state = _admin(request)
    name = _check_name(name)
    client = _client(state)
    try:
        remote_dir = _remote_dir(state)
        rel = (remote_dir + "/" + name) if remote_dir else name
        client.delete(rel)
        return {"ok": True}
    except WebDavError as exc:
        raise HTTPException(400, str(exc))
    finally:
        client.close()
