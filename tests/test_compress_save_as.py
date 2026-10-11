"""压缩结果「另存为角色库新副本」端点：/sessions/{sid}/compress/save-as。

要点：
- 新卡入库、parent_id 指向会话绑定的根角色卡、public=False、id 全新；
- 当前会话副本（override）与库主卡都不动；
- 提案不消费，保存后仍可再 apply 或下载；
- 房间会话为每个受影响 AI 成员各存一张；
- 提案 id 无效 → 404。
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_session import (
    CompressSaveAsBody,
    session_compress_save_as,
)
from flyai.app import AppState
from flyai.config import AppConfig
from flyai.lore.models import Character
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread

UID = "u1"


class _FakeLore:
    def __init__(self, chars=None):
        self.chars = {c.id: c for c in (chars or [])}
        self.saved = []

    def get_character(self, cid):
        return self.chars.get(cid)

    def save_character(self, ch):
        self.saved.append(ch)
        self.chars[ch.id] = ch


def _room_session():
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="小雪", character_id="char-xiaoxue")
    m.override = Character(id="char-xiaoxue", name="小雪", intro="旧简介").to_dict()
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title="小雪", member_ids=[m.id]))
    return s, m


def _wire(state, sc):
    state.user = lambda _r: sc
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_room_save_as_creates_library_copy_without_touching_session(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s, m = _room_session()
    store.save(s)

    root = Character(id="char-xiaoxue", name="小雪", intro="根卡")
    lore = _FakeLore([root])
    state = AppState(config=AppConfig())
    sc = SimpleNamespace(uid=UID, store=store, lore=lore)
    req = _wire(state, sc)

    raw = {"members": [{"member_id": m.id,
                        "character": {"intro": "新简介"}}], "world": None}
    pid = state.put_compress_proposal(UID, s.id, True, raw, {})

    out = session_compress_save_as(s.id, CompressSaveAsBody(proposal_id=pid), req)

    assert len(out["characters"]) == 1
    assert len(lore.saved) == 1
    card = lore.saved[0]
    assert card.name == "小雪"
    assert card.intro == "新简介"
    assert card.public is False
    assert card.parent_id == "char-xiaoxue"
    assert card.id != "char-xiaoxue"
    assert out["characters"][0]["id"] == card.id

    # 当前会话副本原样未动
    refreshed = store.get(s.id)
    assert refreshed.member(m.id).override["intro"] == "旧简介"
    # 库主卡未被覆盖
    assert lore.get_character("char-xiaoxue").intro == "根卡"
    # 提案仍在，可继续应用
    assert state.get_compress_proposal(pid, UID) is not None


def test_room_save_as_bad_pid_404(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s, _m = _room_session()
    store.save(s)
    state = AppState(config=AppConfig())
    sc = SimpleNamespace(uid=UID, store=store, lore=_FakeLore())
    req = _wire(state, sc)

    with pytest.raises(HTTPException) as ei:
        session_compress_save_as(
            s.id, CompressSaveAsBody(proposal_id="not-a-pid"), req
        )
    assert ei.value.status_code == 404
    assert sc.lore.saved == []


def test_single_save_as_sets_parent_to_bound_character(tmp_path, monkeypatch):
    import flyai.lore.gen as gen

    store = JsonSessionStore(tmp_path / "sessions")
    s = Session(title="单聊")
    s.character_id = "c1"
    store.save(s)

    lore = _FakeLore([Character(id="c1", name="阿零", intro="根卡")])
    state = AppState(config=AppConfig())
    sc = SimpleNamespace(uid=UID, store=store, lore=lore)
    req = _wire(state, sc)

    def fake_apply(_char, _world, _raw):
        return Character(id="c1", name="阿零", intro="压缩后"), None

    monkeypatch.setattr(gen, "apply_conversation_compression", fake_apply)

    pid = state.put_compress_proposal(UID, s.id, False, {"x": 1}, {})
    out = session_compress_save_as(s.id, CompressSaveAsBody(proposal_id=pid), req)

    assert len(lore.saved) == 1
    card = lore.saved[0]
    assert card.name == "阿零"
    assert card.intro == "压缩后"
    assert card.parent_id == "c1"
    assert card.id != "c1"
    assert card.public is False
    assert out["characters"][0]["id"] == card.id

    # 主卡态：没有因此生成会话副本
    assert store.get(s.id).character_override is None
    # 提案保留
    assert state.get_compress_proposal(pid, UID) is not None
