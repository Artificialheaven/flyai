"""CDP 验证用临时服务器：加载含已知测试密码的临时配置，不触碰真实 config.json。"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path

import uvicorn

from flyai.app import create_app

tmp_config = Path(__file__).resolve().parent / "tmp_cdp_config.json"

with open("config/config.example.json", encoding="utf-8") as f:
    cfg = json.load(f)
cfg["auth"]["admin"]["password"] = "cdptest"
with open(tmp_config, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

app = create_app(config_path=tmp_config)

uvicorn.Server(
    uvicorn.Config(app, host="127.0.0.1", port=8011, log_level="warning")
).run()
