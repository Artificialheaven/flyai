"""公开的应用元信息：webroot 中可下载的最新 Android 安装包。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..updates import find_latest_apk, resolve_web_root

router = APIRouter(prefix="/api/app", tags=["app"])


@router.get("/version")
def get_app_version(request: Request):
    """公开接口：APK 在登录前后都可能检查更新。"""
    state = request.app.state.flyai
    release = find_latest_apk(resolve_web_root(state.config.server.web_root))

    payload = {
        "version": None,
        "version_code": None,
        "file_name": None,
        "url": None,
        "size": None,
    }
    if release is not None:
        payload.update(
            {
                "version": release.version,
                "version_code": release.version_code,
                "file_name": release.file_name,
                # 相对地址由客户端按自己的 apiBase 拼绝对地址，兼容反代/CDN
                "url": "/" + release.file_name,
                "size": release.size,
            }
        )
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
