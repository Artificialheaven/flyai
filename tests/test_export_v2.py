"""对话导出 v2：线程完整消息（含工具调用/结果）+ 角色卡 Author+ID 标识。"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character
from flyai.schemas import Message, ToolCall
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


class _FakeLore:
    def __init__(self, chars=None):
        self._chars = {c.id: c for c in (chars or [])}

    def get_character(self, cid):
        return self._chars.get(cid)


def _room_with_tool(tmp_path):
    ch = Character(id="c1", name="初", author="admin")
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="初", character_id="c1")
    s.members.append(m)
    t = Thread(kind="dm", title="初", member_ids=[m.id])
    s.threads.append(t)

    t.messages.append(Message(role="user", content="帮我画张图"))
    t.messages.append(
        Message(
            role="assistant", content="好的", name="初",
            tool_calls=[ToolCall(id="tc1", name="generate_image", arguments={"prompt": "JK 少女"})],
        )
    )
    t.messages.append(
        Message(
            role="tool", content="[图片已生成] /uploads/admin/x.png\n一句画面说明",
            name="generate_image", tool_call_id="tc1",
        )
    )

    store = JsonSessionStore(tmp_path / "s")
    store.save(s)
    return s, ch, store


def test_session_export_v2_shape(tmp_path):
    from flyai.api.routes_session import session_export

    s, ch, store = _room_with_tool(tmp_path)
    sc = SimpleNamespace(uid="admin", store=store, lore=_FakeLore([ch]))
    flyai = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=flyai)))

    resp = session_export(s.id, request)
    body = json.loads(resp.body)

    assert body["type"] == "flyai-conversation"
    assert body["version"] == 2
    assert body["name"] == "房间"

    data = body["data"]
    assert data["session"]["id"] == s.id
    assert len(data["members"]) == 2  # 用户 + AI
    assert len(data["threads"]) == 1

    # 线程消息保留全部文本：用户消息、工具调用、工具结果（含标记/URL）
    msgs = data["threads"][0]["messages"]
    blob = json.dumps(msgs, ensure_ascii=False)
    assert "帮我画张图" in blob
    assert "generate_image" in blob and "JK 少女" in blob
    assert "[图片已生成]" in blob and "/uploads/admin/x.png" in blob
    assert "一句画面说明" in blob

    # 角色卡带 Author+ID 复合标识
    assert len(data["characters"]) == 1
    card = data["characters"][0]
    assert card["ref"] == f"admin:{ch.id}"
    assert card["name"] == "初"


# ---- 会话角色卡导出：头像内嵌 ----

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


def _character_export_env(tmp_path, with_upload=True):
    ch = Character(id="c1", name="初", author="admin",
                   avatar="/uploads/admin/avatar.png")
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="初", character_id="c1")
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title="初", member_ids=[m.id]))
    store = JsonSessionStore(tmp_path / "s")
    store.save(s)

    upload_dir = tmp_path / "data" / "uploads" / "admin"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "avatar.png").write_bytes(PNG)

    sc = SimpleNamespace(uid="admin", store=store, lore=_FakeLore([ch]))
    if with_upload:
        sc.upload_dir = upload_dir
    flyai = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=flyai)))
    return s, request


def test_session_character_export_embeds_avatar(tmp_path):
    import base64

    from flyai.api.routes_session import session_character_export

    s, request = _character_export_env(tmp_path)
    body = json.loads(session_character_export(s.id, request).body)
    assert body["type"] == "flyai-character"
    image = body["data"]["avatar_image"]
    assert image["media_type"] == "image/png"
    assert base64.b64decode(image["data"]) == PNG


def test_session_character_export_without_upload_dir(tmp_path):
    """作用域无 upload_dir（老测试桩）→ getattr 兜底，不炸、不带图。"""
    from flyai.api.routes_session import session_character_export

    s, request = _character_export_env(tmp_path, with_upload=False)
    body = json.loads(session_character_export(s.id, request).body)
    assert body["data"]["name"] == "初"
    assert "avatar_image" not in body["data"]
