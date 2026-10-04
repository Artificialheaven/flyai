"""端到端验证：真实调用豆包语音合成 V3（手动执行，需联网，不进 pytest）。

用法：
    python test/e2e_doubao_tts.py
    python test/e2e_doubao_tts.py --text "你好世界" --speaker S_BcqE5org2

从 config/config.json 的 tts 节点读取 key/代理等设置，合成一句话，
断言返回的是可识别的 MP3 字节；打印 request_id / logid / 计费用量 / 字节数。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import load_config
from flyai.tts import DoubaoTtsClient


def _is_mp3(data: bytes) -> bool:
    return (
        data[:3] == b"ID3"
        or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="你好，这是一条豆包语音合成测试。")
    parser.add_argument("--speaker", default="")
    args = parser.parse_args()

    cfg = load_config()
    settings = cfg.tts
    if not settings.api_key:
        print("config/config.json 的 tts.api_key 为空，无法进行 E2E")
        return 2

    client = DoubaoTtsClient(settings)
    t0 = time.time()
    result = await client.synthesize(
        args.text, args.speaker or None
    )
    elapsed = time.time() - t0

    print("request_id :", result.request_id)
    print("logid      :", result.logid)
    print("session_id :", result.session_id)
    print("usage      :", result.usage)
    print("audio bytes:", len(result.audio))
    print("elapsed    : %.2fs" % elapsed)

    failures = []
    if len(result.audio) <= 1000:
        failures.append("音频字节数过少：%d" % len(result.audio))
    if not _is_mp3(result.audio):
        failures.append("返回内容不是可识别的 MP3（起始字节 %r）" % result.audio[:4])

    if failures:
        print("\nE2E 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\nE2E 通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
