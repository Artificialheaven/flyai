"""压缩提案 API：POST /compress 只出提案不写入，POST /compress/apply 校验后写入。"""

import asyncio
import json
import os
import sys
import types as _types
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_session import session_compress, session_compress_apply
from flyai.app import AppState
from flyai.lore.models import Character
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore


class _FakeLore:
    def __init__(self, chars=None):
        self._chars = {c.id: c for c in (chars or [])}

    def get_character(self, cid):
        return self._chars.get(cid)


def _env(tmp_path, uid="admin"):
    store = JsonSessionStore(tmp_path / "s")
    char = Character(id="c1", name="初", intro="旧简介")
    lore = _FakeLore([char])
    sc = SimpleNamespace(uid=uid, level=256, store=store, lore=lore)

    state = SimpleNamespace()
    state._compress_proposals = {}
    state._PROPOSAL_TTL = 1800
    state._PROPOSAL_MAX = 32
    state.user = lambda request: sc
    state.get_provider = lambda *a: object()
    state.put_compress_proposal = _types.MethodType(AppState.put_compress_proposal, state)
    state.get_compress_proposal = _types.MethodType(AppState.get_compress_proposal, state)
    state.drop_compress_proposal = _types.MethodType(AppState.drop_compress_proposal, state)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, char, lore, store


def _patch_complete(monkeypatch, payload):
    import flyai.lore.gen as gen

    async def fake_complete(provider, messages, system_prompt="", **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(gen, "complete", fake_complete)


def _run(coro):
    return asyncio.run(coro)


# ---- 单 AI ----

def test_compress_returns_proposal_without_writing(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    _patch_complete(monkeypatch, {"character": {"intro": "新简介"}})

    s = store.create(title="空白会话")
    store.save(s)

    res = _run(session_compress(s.id, request))
    assert res["room"] is False
    assert res["proposal_id"]
    assert res["before"]["character"]["intro"] == ""
    assert res["after"]["character"]["intro"] == "新简介"
    assert res["diff"]["character"]["intro"]["after"] == "新简介"
    # 未确认：会话没有副本写入
    assert store.get(s.id).character_override is None


def test_compress_apply_writes_override_and_consumes_proposal(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    _patch_complete(monkeypatch, {"character": {"intro": "新简介"}})

    s = store.create(title="空白会话")
    store.save(s)
    res = _run(session_compress(s.id, request))

    out = _run(session_compress_apply(
        s.id, SimpleNamespace(proposal_id=res["proposal_id"]), request
    ))
    assert out["character"]["intro"] == "新简介"
    assert out["scoped"] is True
    reloaded = store.get(s.id)
    assert reloaded.character_override["intro"] == "新简介"
    # 提案已消费
    assert state.get_compress_proposal(res["proposal_id"], "admin") is None


def test_compress_apply_unknown_proposal_404(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    s = store.create(title="t")
    store.save(s)
    with pytest.raises(Exception) as ei:
        _run(session_compress_apply(s.id, SimpleNamespace(proposal_id="nope"), request))
    assert ei.value.status_code == 404


def test_compress_apply_other_user_403(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path, uid="admin")
    _patch_complete(monkeypatch, {"character": {"intro": "新简介"}})
    s = store.create(title="t")
    store.save(s)
    res = _run(session_compress(s.id, request))

    sc.uid = "intruder"
    with pytest.raises(Exception) as ei:
        _run(session_compress_apply(
            s.id, SimpleNamespace(proposal_id=res["proposal_id"]), request
        ))
    assert ei.value.status_code == 403
    # 提案仍在
    assert state.get_compress_proposal(res["proposal_id"], "admin") is not None


def test_compress_apply_wrong_session_400(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    _patch_complete(monkeypatch, {"character": {"intro": "新简介"}})
    s1 = store.create(title="一")
    s2 = store.create(title="二")
    store.save(s1)
    store.save(s2)
    res = _run(session_compress(s1.id, request))
    with pytest.raises(Exception) as ei:
        _run(session_compress_apply(
            s2.id, SimpleNamespace(proposal_id=res["proposal_id"]), request
        ))
    assert ei.value.status_code == 400


# ---- 房间 ----

def _room(char):
    from flyai.sessions.models import RoomMember, Session, Thread

    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name=char.name, character_id=char.id)
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    return s, m


def test_room_compress_then_apply(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    s, ai = _room(char)
    store.save(s)

    _patch_complete(monkeypatch, {
        "members": [{"member_id": ai.id, "character": {"intro": "新简介"}}],
        "world": {},
    })

    res = _run(session_compress(s.id, request))
    assert res["room"] is True
    # 未确认：成员无 override
    assert store.get(s.id).members[1].override is None

    out = _run(session_compress_apply(
        s.id, SimpleNamespace(proposal_id=res["proposal_id"]), request
    ))
    assert out["room"] is True
    assert store.get(s.id).members[1].override["intro"] == "新简介"


def test_room_compress_apply_409_when_base_changed(tmp_path, monkeypatch):
    state, sc, request, char, lore, store = _env(tmp_path)
    s, ai = _room(char)
    store.save(s)

    _patch_complete(monkeypatch, {
        "members": [{"member_id": ai.id, "character": {"intro": "新简介"}}],
        "world": {},
    })
    res = _run(session_compress(s.id, request))

    # 提案后库卡内容变化 → 签名不一致
    lore._chars["c1"] = Character(id="c1", name="初", intro="被改动的简介")

    with pytest.raises(Exception) as ei:
        _run(session_compress_apply(
            s.id, SimpleNamespace(proposal_id=res["proposal_id"]), request
        ))
    assert ei.value.status_code == 409
    # 409 不消费提案
    assert state.get_compress_proposal(res["proposal_id"], "admin") is not None
    # 未写入
    assert store.get(s.id).members[1].override is None
