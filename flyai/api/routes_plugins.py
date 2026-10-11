"""插件与工具清单接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import feature_prefs

router = APIRouter(prefix="/api", tags=["plugins"])


@router.get("/plugins")
def list_plugins(request: Request):
    state = request.app.state.flyai
    plugins = []
    for info in state.loader.list_status():
        plugins.append(
            {
                "name": info.name,
                "version": info.version,
                "description": info.description,
                "author": info.author,
                "loaded": info.loaded,
                "error": info.error,
                "tools": info.tools,
            }
        )
    return {"plugins": plugins}


@router.get("/tools")
def list_tools(request: Request):
    state = request.app.state.flyai
    # 当前用户真实可暴露集（全局/用户功能类别开关 + 工具总开关）；
    # 输入框「⋯ → 工具」清单以此为准，前端不重复权限规则
    sc = state.user(request)
    _us = getattr(sc, "user_settings", None)
    allowed = feature_prefs.allowed_tool_names(state.registry, state.config, _us)
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "category": feature_prefs.TOOL_CATEGORIES.get(t.name, "base"),
                "allowed": t.name in (allowed or set()),
            }
            for t in state.registry.all()
        ]
    }