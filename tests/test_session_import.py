"""会话导入：flyai-conversation v2 导出包 → 全新会话；卡复用/降级副本策略。"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_session import SessionImportPayload, import_session, session_export
from flyai.lore.models import Character, World
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


class _FakeLore:
    def __init__(self, chars=None, worlds=None):
        self._chars = {c.id: c for c in (chars or [])}
        self._worlds = {w.id: w for w in (worlds or [])}

    def get_character(self, cid):
        return self._chars.get(cid)

    def get_world(self, wid):
        return self._worlds.get(wid)


def _env(tmp_path, chars_in_library):
    store = JsonSessionStore(tmp_path / "s")
    s = Session(title="原房间")
    world = World(id="w1", name="测试世界")
    s.world_id = world.id
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(2):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        t = Thread(kind="dm", title=m.name, member_ids=[m.id])
        from flyai.schemas import Message
        t.messages.append(Message(role="user", content="你好%d" % i))
        t.messages.append(Message(role="assistant", content="回复%d" % i))
        s.threads.append(t)
    ais = list(s.members[1:])
    s.threads.append(Thread(kind="group", title="群聊",
                            member_ids=[m.id for m in ais]))
    store.save(s)

    # 导出端的 lore 永远含全部卡；导入端的 lore 按用例决定
    export_lore = _FakeLore(chars, [world])
    sc = SimpleNamespace(uid="admin", level=256, store=store, lore=export_lore)
    state = SimpleNamespace()
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))

    resp = session_export(s.id, request)
    envelope = json.loads(resp.body)

    # 切换为导入环境（新 store，lore 按用例）
    new_store = JsonSessionStore(tmp_path / "s2")
    imp_lore = _FakeLore(
        chars if chars_in_library else [],
        [world] if chars_in_library else [],
    )
    sc2 = SimpleNamespace(uid="admin", level=256, store=new_store, lore=imp_lore)
    state2 = SimpleNamespace()
    state2.user = lambda request: sc2
    request2 = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state2)))
    return envelope, new_store, request2


def _call(envelope, request):
    payload = SessionImportPayload(**envelope)
    return import_session(payload, request)


def test_import_reuses_library_cards_and_world(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=True)
    out = _call(envelope, request)

    assert out["id"] != envelope["data"]["session"]["id"]  # 新会话 id
    assert out["import_summary"] == {"reused_cards": 2, "scoped_cards": 0}
    assert out["world_id"] == "w1"
    assert not out["pinned"]

    s2 = store.get(out["id"])
    assert s2.title == "原房间"
    assert len(s2.threads) == 3
    dm = s2.threads[0]
    assert [m.role for m in dm.messages] == ["user", "assistant"]
    assert dm.messages[0].content == "你好0"
    # 成员保持库引用，无副本
    ai = [m for m in s2.members if not m.is_user]
    assert all(m.character_id and not m.override for m in ai)


def test_import_missing_cards_become_scoped(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    out = _call(envelope, request)

    assert out["import_summary"] == {"reused_cards": 0, "scoped_cards": 2}
    s2 = store.get(out["id"])
    ai = [m for m in s2.members if not m.is_user]
    assert all(not m.character_id and m.override for m in ai)
    # 副本卡内容就是随包卡；导出用的 ref 字段不应落进副本
    assert ai[0].override["id"] in ("c1", "c2")
    assert "ref" not in ai[0].override
    # 世界同样降为会话副本
    assert not s2.world_id and s2.world_override
    # 窗口仍能按成员 id 关联（成员保留原 id）
    assert s2.threads[-1].member_ids == [m.id for m in ai]


def test_import_twice_creates_two_sessions(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    a = _call(envelope, request)
    b = _call(envelope, request)
    assert a["id"] != b["id"]
    assert len(store.list()) == 2  # 没有互相覆盖


def test_import_rejects_bad_type_and_version(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=False)
    bad = dict(envelope)
    bad["type"] = "other"
    with pytest.raises(HTTPException) as ei:
        _call(bad, request)
    assert ei.value.status_code == 400

    bad = dict(envelope)
    bad["version"] = 1
    with pytest.raises(HTTPException) as ek:
        _call(bad, request)
    assert ek.value.status_code == 400


def test_import_preserves_existing_member_override(tmp_path):
    envelope, store, request = _env(tmp_path, chars_in_library=True)
    # 导出口径：把第一个 AI 成员改成自带副本（override）
    m0 = envelope["data"]["members"][1]
    card = next(c for c in envelope["data"]["characters"] if c["id"] == m0["character_id"])
    m0["override"] = {k: v for k, v in card.items() if k != "ref"}
    m0["character_id"] = ""

    out = _call(envelope, request)
    s2 = store.get(out["id"])
    target = s2.member(m0["id"])
    assert target.override and not target.character_id
    # 另一成员仍按库引用计入复用
    assert out["import_summary"]["reused_cards"] == 1
