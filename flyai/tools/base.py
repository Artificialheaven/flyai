"""Tool 基类、结果、上下文与 @tool 装饰器。"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolResult:
    """工具执行结果，output 返回给模型，meta 供前端展示。"""

    output: str
    ok: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """工具执行上下文：白名单、插件配置、会话信息、日志。

    各工具依据此上下文自主声明并执行自身的安全边界。
    """

    allowed_read_dirs: List[Path] = field(default_factory=list)
    plugin_config: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    # 当前聊天运行 id（交互式工具据此关联用户响应）
    run_id: str = ""
    tool_settings: Optional[Any] = None
    # 按请求注入的服务，如 {'lore': LoreQuerier}
    services: Dict[str, Any] = field(default_factory=dict)
    logger: Any = field(default_factory=lambda: logging.getLogger("flyai.tool"))
    # 调用者身份（P0）：uid / 权限等级 / 是否管理员，注册表据此做执行期授权
    uid: str = ""
    level: int = 0
    is_admin: bool = False


class Tool(ABC):
    """工具基类。子类声明 name/description/parameters(JSON Schema) 并实现 run。"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    # 交互式工具（如等待用户选择）可能阻塞数分钟，注册表不对其施加超时
    interactive: bool = False
    # 风险工具：注册表在执行期做权限校验（specs 只控制模型可见性，不负责授权）。
    # risk=True 且 min_level<=0 时仅管理员可用；min_level=N>0 时等级≥N 或管理员可用。
    risk: bool = False
    min_level: int = 0

    @abstractmethod
    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    def spec_openai(self) -> Dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class FunctionTool(Tool):
    """把被 @tool 装饰的普通 async 函数包装成 Tool。"""

    def __init__(self, name: str, description: str, parameters: Dict, func: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self._func = func

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        result = self._func(**arguments)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolResult):
            return result
        return ToolResult(output=str(result))


def tool(name: str, description: str, parameters: Dict):
    """装饰一个普通 async 函数，将其注册为工具。"""

    def decorator(func: Callable) -> FunctionTool:
        return FunctionTool(name, description, parameters, func)

    return decorator