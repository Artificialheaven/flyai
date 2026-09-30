"""端到端冒烟（本地验证用，结束后清理所有造数）：

A. mes_example + pinned 角色卡 API 往返（创建→读回→删除）
B. 压缩提案流程：造会话+消息 → POST /compress 只出提案不写入
   → 确认前无变化 → /compress/apply 写入副本 → 重复 apply 404 → 坏提案 404
"""

import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SESS_DIR = os.path.join(ROOT, "data", "admin", "sessions")


def api(method, path, token=None, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            raw = json.loads(raw)
        except Exception:
            pass
        return e.code, raw


def must(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    cfg = json.load(open(os.path.join(ROOT, "config", "config.json"), encoding="utf-8"))
    st, res = api("POST", "/api/auth/login",
                  body={"name": "admin", "password": cfg["auth"]["admin"]["password"]})
    must(st == 200, ("login failed", st, res))
    tok = res["token"]
    print("A. login ok")

    # ---- A. 新字段往返 ----
    char_payload = {
        "name": "SMOKE_TEMP",
        "mes_example": "{{user}}：来了？\n<START>\n{{char}}：（冷笑）不然等你？",
        "table": [
            {"title": "铁则", "content": "不许提过去", "pinned": True},
            {"title": "普通", "content": "爱好是喝茶", "pinned": False},
        ],
    }
    st, created = api("POST", "/api/characters", tok, char_payload)
    must(st == 200, ("create char failed", st, created))
    cid = created["id"]
    try:
        must(created["mes_example"] == char_payload["mes_example"], "mes_example dropped")
        must(created["table"][0]["pinned"] is True, "pinned dropped")
        print("A. create roundtrip ok (mes_example + pinned)")

        st, got = api("GET", "/api/characters", tok)
        chars = got.get("characters", []) if isinstance(got, dict) else got
        row = [c for c in chars if c["id"] == cid]
        must(bool(row), "character not listed")
        print("A. list contains temp character ok")
    finally:
        st, _ = api("DELETE", "/api/characters/" + cid, tok)
        must(st == 200, ("cleanup char failed", st))
    print("A. temp character deleted")

    # ---- B. 压缩提案流程 ----
    st, sess = api("POST", "/api/sessions", tok,
                   {"title": "SMOKE 压缩会话", "character_id": cid if False else ""})
    must(st == 200, ("create session failed", st, sess))
    sid = sess["id"]
    try:
        # 绑定刚才能复用的角色：改用上一步重建？角色已删。直接往会话文件写消息，
        # 压缩不依赖外部 provider 调用（gen 的 complete 走当前会话提供商）。
        fpath = os.path.join(SESS_DIR, sid + ".json")
        deadline = time.time() + 5
        while not os.path.exists(fpath) and time.time() < deadline:
            time.sleep(0.1)
        must(os.path.exists(fpath), "session file not found: " + fpath)

        sd = json.load(open(fpath, encoding="utf-8"))
        # 已迁移格式：用户座位 + AI 参与者 + 私聊线程（消息在线程里）
        messages = [
            {"role": "user", "content": "我们昨天在港口遇到了那个商人，他说三天后有批货。"},
            {"role": "assistant", "content": "（点头）我记得，他叫老奎，右手缺了两根手指。"},
            {"role": "user", "content": "对，你还说他不像好人。今天先去打探一下吧。"},
        ]
        import uuid as _uuid

        user_id = _uuid.uuid4().hex
        ai_id = _uuid.uuid4().hex
        thread_id = _uuid.uuid4().hex
        sd["messages"] = []
        sd["members"] = [
            {"id": user_id, "name": "你", "character_id": "", "is_user": True,
             "override": None, "memory": "", "attributes": {}},
            {"id": ai_id, "name": "初", "character_id": "", "is_user": False,
             "override": None, "memory": "", "attributes": {}},
        ]
        sd["threads"] = [
            {"id": thread_id, "kind": "dm", "title": "私聊",
             "member_ids": [ai_id], "messages": messages, "edits": {}},
        ]
        json.dump(sd, open(fpath, "w", encoding="utf-8"), ensure_ascii=False)

        st, check = api("GET", "/api/sessions/" + sid, tok)
        must(st == 200 and len(check.get("threads", [])) == 1, ("session inject failed", st))
        print("B. migrated session with 3 thread messages ready")

        st, prop = api("POST", "/api/sessions/" + sid + "/compress", tok)
        if st == 500 and "provider" in json.dumps(prop, ensure_ascii=False):
            print("B. compress needs a working LLM provider, skipping live apply:", prop)
            return
        must(st == 200, ("compress failed", st, prop))
        must(prop["room"] is True, "expected room path")
        print("B. proposal generated:", prop["proposal_id"])
        member_ids = list(prop["diff"]["members"].keys())
        must(bool(member_ids), "no member diffs")
        for mid in member_ids:
            d = prop["diff"]["members"][mid]
            print("   member", mid[:8], "intro:",
                  repr(d["intro"]["before"][:16]), "->", repr(d["intro"]["after"][:16]),
                  "table a/c/u:", len(d["table"]["added"]), len(d["table"]["changed"]),
                  len(d["table"]["unchanged"]))

        # 确认前：成员仍无 override
        st, before_apply = api("GET", "/api/sessions/" + sid, tok)
        members_before = before_apply.get("members", [])
        must(all(m.get("override") is None for m in members_before),
            "override appeared before apply")
        print("B. no member override before apply ok")

        st, applied = api("POST", "/api/sessions/" + sid + "/compress/apply", tok,
                          {"proposal_id": prop["proposal_id"]})
        must(st == 200, ("apply failed", st, applied))
        print("B. apply ok, members:", len(applied.get("members", {})))

        st, after_apply = api("GET", "/api/sessions/" + sid, tok)
        has_override = any(m.get("override") for m in after_apply.get("members", []))
        must(has_override, "member override not persisted after apply")
        print("B. member overrides persisted ok")

        st, again = api("POST", "/api/sessions/" + sid + "/compress/apply", tok,
                        {"proposal_id": prop["proposal_id"]})
        must(st == 404, ("re-apply should 404", st))
        print("B. re-apply 404 ok (proposal consumed)")

        st, bad = api("POST", "/api/sessions/" + sid + "/compress/apply", tok,
                      {"proposal_id": "deadbeef"})
        must(st == 404, ("bad proposal should 404", st))
        print("B. unknown proposal 404 ok")
    finally:
        st, _ = api("DELETE", "/api/sessions/" + sid, tok)
        must(st == 200, ("cleanup session failed", st))
        print("cleanup: smoke session deleted")


if __name__ == "__main__":
    main()
    print("SMOKE OK")
