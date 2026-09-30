"""探测：火山方舟 Responses API 内置 web_search（模型提供方实现的联网搜索）。

直接运行：python test/probe_ark_web_search.py
会依次跑：
  ① 非流式调用 —— 拿出完整 response 结构（output items / 引用 / usage）
  ② 流式调用   —— 拿出 SSE 事件链（web_search_call 生命周期 + 正文增量）
  ③ function 工具混用探测 —— 看 web_search 能否与客户端 function tool 共存

API Key 只从 config/config.json 里读（volces.com 提供商），不打印。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 可用环境变量覆盖：ARK_BASE / ARK_MODEL
BASE_URL = os.environ.get("ARK_BASE", "https://ark.cn-beijing.volces.com/api/v3")
MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-1-pro-260628")
CODING_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
QUESTION = "北京今天（2026年9月28日）的天气怎么样？气温多少？"
DUMP_FILE = Path(__file__).parent / "temp" / "ark_web_search_dump.json"


def read_ark_key() -> str:
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    for p in data.get("providers", []):
        if "volces.com" in (p.get("base_url") or "") and p.get("api_key"):
            return p["api_key"]
    return ""


def banner(title):
    print("\n" + "=" * 20 + " " + title + " " + "=" * 20)


def coding_contrast(headers):
    """Coding 套餐端点证据：200 接受 web_search，但 output 无 web_search_call。

    第三方模型（deepseek/kimi）和经该端点路由的 doubao-seed 均不执行托管搜索。
    """
    banner("④ Coding 套餐端点对比 POST /api/coding/v3/responses")
    q = [{"role": "user", "content": "今天有什么热点新闻？一句话回答"}]

    # 非流式：检查 output items 类型
    for model in ("deepseek-v4.1-flash", "kimi-k3", "doubao-seed-2-1-pro"):
        body = {"model": model, "input": q, "tools": [{"type": "web_search"}]}
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(CODING_URL + "/responses", headers=headers, json=body)
        if resp.status_code >= 400:
            print("%-25s -> %s %s" % (model, resp.status_code, resp.text[:200]))
            continue
        d = resp.json()
        types = [i.get("type") for i in d.get("output", [])]
        searched = "web_search_call" in types
        print("%-25s -> %s served=%s items=%s 实际搜索=%s"
              % (model, resp.status_code, d.get("model"), types, searched))

    # 流式：事件链里无 web_search_call.* 即未触发搜索
    body = {
        "model": "doubao-seed-2-1-pro",
        "stream": True,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "今天有什么热点新闻"}
        ]}],
        "tools": [{"type": "web_search"}],
    }
    events = []
    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", CODING_URL + "/responses",
                           headers=headers, json=body) as resp:
            ev = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                    if not events or events[-1] != ev:
                        events.append(ev)
    print("\n流式事件链（doubao-seed-2-1-pro @ coding）：")
    for e in events:
        mark = "  <<< 搜索事件" if "web_search" in e else ""
        print("  ", e + mark)
    print("结论：事件链中无任何 web_search_call 事件 → 托管联网搜索未执行。")


def main():
    key = read_ark_key()
    if not key:
        print("ERROR: config 里没找到 volces.com 提供商 key")
        return 1
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    DUMP_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ---------- ① 非流式 ----------
    banner("① 非流式 POST /responses")
    body = {
        "model": MODEL,
        "input": [{"role": "user", "content": QUESTION}],
        "tools": [{"type": "web_search"}],
    }
    print("REQUEST BODY:", json.dumps(body, ensure_ascii=False))
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(BASE_URL + "/responses", headers=headers, json=body)
    print("STATUS:", resp.status_code)
    if resp.status_code >= 400:
        print(resp.text[:2000])
        print(
            "\n[!] 标准端点模型未开通：请到 Ark 控制台开通 doubao-seed-2-1-pro"
            "（按量计费）后重跑本脚本；下面继续演示 Coding 套餐端点的「静默忽略」行为。"
        )
        coding_contrast(headers)
        return 1
    data = resp.json()
    DUMP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完整响应已 dump:", DUMP_FILE)

    print("\n-- 顶层字段 --")
    print(sorted(data.keys()))
    print("\n-- output items --")
    for item in data.get("output", []):
        itype = item.get("type")
        print("  * type:", itype, "| id:", item.get("id"), "| status:", item.get("status"))
        if itype == "web_search_call":
            print("    action:", json.dumps(item.get("action"), ensure_ascii=False))
            for k in item:
                if k not in ("id", "type", "action", "status"):
                    v = item[k]
                    print("    %s:" % k, json.dumps(v, ensure_ascii=False)[:500])
        if itype == "message":
            for part in item.get("content", []):
                ptype = part.get("type")
                text = part.get("text", "")
                anns = part.get("annotations") or []
                print("    part:", ptype, "| text 长度:", len(text))
                print("    正文:", text[:800])
                print("    annotations 数量:", len(anns))
                for a in anns:
                    print("      -", json.dumps(a, ensure_ascii=False)[:400])
    print("\n-- usage --")
    print(json.dumps(data.get("usage"), ensure_ascii=False))
    for k in ("status", "instructions", "metadata", "previous_response_id"):
        if k in data:
            print("%s:" % k, data[k])

    # ---------- ② 流式 ----------
    banner("② 流式 POST /responses (stream=true)")
    body2 = dict(body)
    body2["stream"] = True
    event_types = []
    samples = {}
    text_deltas = 0
    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", BASE_URL + "/responses", headers=headers,
                           json=body2) as resp:
            print("STATUS:", resp.status_code)
            event = ""
            for line in resp.iter_lines():
                if not line:
                    event = ""
                    continue
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                    event_types.append(event)
                elif line.startswith("data:"):
                    payload = line.split(":", 1)[1].strip()
                    try:
                        d = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event not in samples:
                        samples[event] = d
                    if event == "response.output_text.delta":
                        text_deltas += 1
    print("\n-- SSE 事件序列（去重保序）--")
    seen = []
    for e in event_types:
        if not seen or seen[-1] != e:
            seen.append(e)
    for e in seen:
        print("  ", e)
    print("output_text.delta 帧总数:", text_deltas)
    print("\n-- 关键事件首帧样例 --")
    for e in (
        "response.output_item.added",
        "response.web_search_call.in_progress",
        "response.web_search_call.searching",
        "response.web_search_call.completed",
        "response.output_item.done",
        "response.completed",
    ):
        if e in samples:
            print("[%s]" % e)
            print(json.dumps(samples[e], ensure_ascii=False)[:700])

    # ---------- ③ 混用客户端 function tool ----------
    banner("③ web_search + 客户端 function 混用")
    body3 = {
        "model": MODEL,
        "input": [{"role": "user", "content": QUESTION}],
        "tools": [
            {"type": "web_search"},
            {
                "type": "function",
                "name": "get_local_weather",
                "description": "查询本地天气的客户端工具",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        ],
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(BASE_URL + "/responses", headers=headers, json=body3)
    print("STATUS:", resp.status_code)
    if resp.status_code >= 400:
        print(resp.text[:1500])
    else:
        d3 = resp.json()
        for item in d3.get("output", []):
            t = item.get("type")
            print("  * type:", t, "| status:", item.get("status"))
            if t == "function_call":
                print("    name:", item.get("name"), "| arguments:", item.get("arguments"))
            if t == "web_search_call":
                print("    action:", json.dumps(item.get("action"), ensure_ascii=False))
            if t == "message":
                for part in item.get("content", []):
                    print("    text:", (part.get("text") or "")[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
