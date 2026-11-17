"""应用层异常定义。"""


class FlyAIError(Exception):
    """应用基础异常。"""


class ProviderError(FlyAIError):
    """提供商通信错误（HTTP 错误/限流/断流等），通常为致命错误。"""

    def __init__(self, message, status_code: int = None, detail: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class ConfigError(FlyAIError):
    """配置加载/校验错误。"""


class ToolError(FlyAIError):
    """工具执行时的参数错误，由 registry 捕获后转为失败 ToolResult。"""


class DeepSeekError(FlyAIError):
    """DeepSeek 官方接口错误；调用方通常据此回退现有实现。"""

    def __init__(self, message, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code