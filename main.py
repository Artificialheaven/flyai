"""FlyAI 启动入口。运行此文件即可在浏览器打开 http://127.0.0.1:8000。

首次启动会自动从 config/config.example.json 生成 config/config.json。
"""

from flyai import app
from flyai.config import AppConfig, load_config
import uvicorn


def main():
    cfg = load_config()
    uvicorn.run(
        "flyai.app:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
    )


if __name__ == "__main__":
    main()