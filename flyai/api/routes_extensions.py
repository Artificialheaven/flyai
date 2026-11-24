"""酒馆扩展（L1 逻辑型）管理 API 与静态资源路由。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..extensions.installer import InstallError
from ..ids import InvalidName, check_safe_name
from ..user import ADMIN_UID

router = APIRouter(prefix="/api/extensions", tags=["extensions"])
static_router = APIRouter(tags=["extensions"])

_MIME_WHITELIST = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


class OrderBody(BaseModel):
    order: List[str] = []


class SettingsBody(BaseModel):
    settings: Dict[str, Any] = {}


class EnableBody(BaseModel):
    enabled: bool = True


def _svc(request: Request):
    service = getattr(request.app.state.flyai, "extensions", None)
    if service is None:
        raise HTTPException(503, "扩展功能未启用")
    return service


def _scope(request: Request):
    return request.app.state.flyai.user(request)


def _cfg(request: Request):
    return getattr(request.app.state.flyai.config, "extensions", None)


def _is_admin(sc) -> bool:
    return sc.uid == ADMIN_UID or int(getattr(sc, "level", 0) or 0) >= 256


def _require_admin(request: Request) -> None:
    sc = _scope(request)
    cfg = _cfg(request)
    min_level = int(getattr(cfg, "min_level", 256) or 256)
    if sc.uid == ADMIN_UID or int(getattr(sc, "level", 0) or 0) >= min_level:
        return
    raise HTTPException(403, "仅管理员可以安装/更新/卸载扩展")


def _feature_enabled(request: Request) -> bool:
    cfg = _cfg(request)
    return bool(getattr(cfg, "enabled", True))


@router.get("")
def list_extensions(request: Request):
    if not _feature_enabled(request):
        return {"extensions": [], "enabled": False}
    sc = _scope(request)
    return {
        "extensions": _svc(request).list_for(sc.uid),
        "enabled": True,
        "admin": _is_admin(sc),
    }


@router.get("/enabled")
def enabled_extensions(request: Request):
    """聊天页宿主启动快照：只含当前用户启用且可加载的扩展。"""
    if not _feature_enabled(request):
        return {"extensions": [], "enabled": False}
    sc = _scope(request)
    return {"extensions": _svc(request).enabled_for(sc.uid), "enabled": True}


@router.get("/{name}")
def get_extension(name: str, request: Request):
    if not _feature_enabled(request):
        raise HTTPException(404, "扩展功能未启用")
    try:
        name = check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    detail = _svc(request).detail(_scope(request).uid, name)
    if detail is None:
        raise HTTPException(404, "扩展不存在")
    return detail


@router.post("/install")
async def install_extension(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    cfg = _cfg(request)
    limit = int(getattr(cfg, "max_total_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024)
    raw = await file.read()
    if len(raw) > limit:
        raise HTTPException(413, "压缩包超过限制")
    fallback = Path(file.filename or "").stem
    try:
        result = _svc(request).install(raw, fallback_name=fallback, update=False)
    except InstallError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "extension": result.manifest.summary(),
        "compat": result.report.to_dict(),
        "warnings": result.warnings,
    }


@router.post("/{name}/update")
async def update_extension(name: str, request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    raw = await file.read()
    try:
        result = _svc(request).install(raw, fallback_name=name, update=True)
    except InstallError as exc:
        raise HTTPException(400, str(exc))
    if result.manifest.name != name:
        raise HTTPException(400, "更新包的扩展名与目标不一致")
    return {
        "ok": True,
        "extension": result.manifest.summary(),
        "compat": result.report.to_dict(),
        "warnings": result.warnings,
    }


@router.delete("/{name}")
def uninstall_extension(name: str, request: Request, purge_settings: bool = False):
    _require_admin(request)
    sc = _scope(request)
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    try:
        return _svc(request).uninstall(name, purge_settings_uid=sc.uid if purge_settings else "")
    except InstallError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{name}/enable")
def enable_extension(name: str, body: EnableBody, request: Request):
    sc = _scope(request)
    cfg = _cfg(request)
    if not _is_admin(sc) and not bool(getattr(cfg, "user_enable_allowed", True)):
        raise HTTPException(403, "当前用户不能自行启用扩展")
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    try:
        return _svc(request).set_enabled(sc.uid, name, bool(body.enabled))
    except InstallError as exc:
        raise HTTPException(400, str(exc))


@router.post("/{name}/disable")
def disable_extension(name: str, request: Request):
    sc = _scope(request)
    cfg = _cfg(request)
    if not _is_admin(sc) and not bool(getattr(cfg, "user_enable_allowed", True)):
        raise HTTPException(403, "当前用户不能自行停用扩展")
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    return _svc(request).set_enabled(sc.uid, name, False)


@router.put("/order")
def reorder_extensions(body: OrderBody, request: Request):
    sc = _scope(request)
    for name in body.order:
        try:
            check_safe_name(name, "扩展名称")
        except InvalidName as exc:
            raise HTTPException(400, str(exc))
    return _svc(request).set_order(sc.uid, body.order)


@router.get("/{name}/settings")
def get_extension_settings(name: str, request: Request):
    sc = _scope(request)
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    return {"settings": _svc(request).get_settings(sc.uid, name)}


@router.put("/{name}/settings")
def put_extension_settings(name: str, body: SettingsBody, request: Request):
    sc = _scope(request)
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    try:
        return _svc(request).put_settings(sc.uid, name, body.settings)
    except InstallError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(413, str(exc))


@router.get("/{name}/host-state")
def extension_host_state(name: str, request: Request):
    sc = _scope(request)
    try:
        check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    state = _svc(request).host_state(sc.uid, name)
    if state is None:
        raise HTTPException(404, "扩展不存在")
    return state


def _safe_asset_path(path: str) -> List[str]:
    raw = str(path or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    if not parts or any(p in (".", "..") or ":" in p or "\x00" in p for p in parts):
        raise HTTPException(400, "非法资源路径")
    return parts


@static_router.get("/scripts/extensions/third-party/{name}/{path:path}")
def extension_static(name: str, path: str, request: Request):
    try:
        name = check_safe_name(name, "扩展名称")
    except InvalidName as exc:
        raise HTTPException(400, str(exc))
    service = _svc(request)
    files = service.store.files_dir(name)
    if not files.is_dir():
        raise HTTPException(404, "扩展不存在")
    parts = _safe_asset_path(path)
    target = files.joinpath(*parts)
    try:
        resolved = target.resolve()
        root = files.resolve()
    except OSError:
        raise HTTPException(404, "资源不存在")
    if resolved != root and root not in resolved.parents:
        raise HTTPException(400, "非法资源路径")
    if not resolved.is_file():
        raise HTTPException(404, "资源不存在")
    ext = resolved.suffix.lower()
    media = _MIME_WHITELIST.get(ext) or mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=300",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    return FileResponse(str(resolved), media_type=media, headers=headers)
