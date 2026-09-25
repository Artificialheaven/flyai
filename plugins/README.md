# FlyAI 插件开发指南

插件是扩展 FlyAI 工具能力的机制。每个插件是一个目录，含 `manifest.json`、`__init__.py`，
可选 `plugin.py`（具体逻辑）。启动时由 `PluginLoader` 扫描 `tools.plugin_dirs`（默认 `plugins`）
下的子目录加载。

## 最小插件结构

```
plugins/my_plugin/
├── manifest.json
└── __init__.py
```

`manifest.json`：

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "description": "插件做什么",
  "author": "你"
}
```

`__init__.py` 定义 `setup(registry, plugin_config)`：

```python
from flyai.tools import FunctionTool, ToolResult, ToolContext


async def _get_time(arguments, ctx: ToolContext) -> ToolResult:
    import datetime
    return ToolResult(output=f"现在时间: {datetime.datetime.now()}")

TIME_TOOL = FunctionTool(
    name="get_time",
    description="获取当前日期时间",
    parameters={"type": "object", "properties": {}},
    func=_get_time,
)


def setup(registry, plugin_config):
    registry.register(TIME_TOOL)
```

## 两种工具形态

1. **继承 `Tool` 基类**：声明 `name/description/parameters` 类属性，实现 `async run(arguments, ctx)`。
   适用于需要构造参数（如白名单目录）的工具，见 `plugins/file_reader/plugin.py`。
2. **`@tool` 装饰器 或 `FunctionTool`**：包装普通 async 函数，写起来最简短，见上面示例。

## 工具声明契约

- 工具需声明 `parameters`，标准 JSON Schema，字段 `additionalProperties: false` 最好。
- `name` 全局唯一，重名会拒绝注册。
- `run` 的 `ctx`（`ToolContext`）提供：
  - `ctx.allowed_read_dirs`: 列表[Path]，配置允许的访问目录（读取类工具应据此做白名单校验）。
  - `ctx.plugin_config`: 插件配置（v1 为空 dict）。
  - `ctx.tool_settings`: 全局工具配置（含 timeout_seconds 等）。
  - `ctx.session_id`: 当前会话 ID。
- 返回 `ToolResult(output, ok=True, meta={})`；`ok=False` 会在 Anthropic 侧标为工具错误结果。

## 安全

- 任何访问本地文件/网络的工具都应借助 `ToolContext` 声明并实施自己的安全边界，
  参考 `file_reader` 用 `Path.resolve()` + 白名单 `is_relative_to()` 防目录穿越。
- 插件代码运行在服务进程内，没有沙箱隔离；只在你信任本机插件的前提下使用。