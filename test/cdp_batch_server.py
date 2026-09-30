# -*- coding: utf-8 -*-
"""本批次 CDP 验证服务器：临时配置 + 固定登录 token + 脚本化压缩 complete。

- 端口 8011，数据仍用 data/admin（造数全部带 ZB 前缀，浏览器侧负责清理）
- 压缩相关的 flyai.lore.gen.complete 替换为按标题合并的固定提案 JSON
- create_app 带 lifespan，on_event("startup") 不会执行，
  补丁在首个 HTTP 请求的一次性中间件里完成（此时 AppState 已就绪）
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path

import uvicorn

from flyai.app import create_app
from flyai.user import UserScope

tmp_config = Path(__file__).resolve().parent / "temp" / "zb_cdp_config.json"
tmp_config.parent.mkdir(parents=True, exist_ok=True)

with open("config/config.example.json", encoding="utf-8") as f:
    cfg = json.load(f)
cfg["auth"]["admin"]["password"] = "cdptest"
# 激活一个提供商：compress_room 先检查 provider 非空才会走到（已被替换的）complete
cfg["active_provider"] = "deepseek"
with open(tmp_config, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

app = create_app(config_path=tmp_config)

_HEX_RE = re.compile(r"[0-9a-f]{32}")
_patched = {"done": False}


@app.middleware("http")
async def cdp_one_time_patch(request, call_next):
    if not _patched["done"]:
        import flyai.lore.gen as gen

        state = request.app.state.flyai

        # 固定 admin scope，绕过登录鉴权（state.user 原本按 Bearer 解析）
        admin_scope = UserScope.build("admin", state.config, state.data_dir, state.user_store)
        state.user = lambda req: admin_scope

        # 固定 token 写入文件供浏览器驱动注入 localStorage
        token = state.auth_sessions.issue("admin", "admin", "admin", 256)
        token_file = Path(__file__).resolve().parent / "temp" / "zb_cdp_token.txt"
        token_file.write_text(token, encoding="utf-8")

        async def fake_complete(provider, messages, system_prompt="", **kwargs):
            m = _HEX_RE.findall(str(messages[-1].content))
            mid = m[0] if m else ""
            payload = {
                "members": [
                    {
                        "member_id": mid,
                        "character": {
                            "intro": "新简介：观察力敏锐的伙伴",
                            "table": [
                                {"title": "铁则", "content": "新铁则内容：绝不在夜里提灯"},
                                {"title": "新线索", "content": "老奎三天后的货有问题"},
                            ],
                        },
                    }
                ],
                "world": {},
            }
            return json.dumps(payload, ensure_ascii=False)

        gen.complete = fake_complete
        _patched["done"] = True
        print("cdp server patched, token:", token[:10], flush=True)

    return await call_next(request)


uvicorn.Server(
    uvicorn.Config(app, host="127.0.0.1", port=8011, log_level="warning")
).run()
