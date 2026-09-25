"""插件与工具清单接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request

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
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in state.registry.all()
        ]
    }