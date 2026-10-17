"""POST /api/rooms/{rid}/members/{mid}/character/publish：座位副本入库。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from fastapi import HTTPException

from flyai.api.routes_room import publish_member_character
from flyai.lore.models import Character
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


class _FakeLore:
    def __init__(self, chars=None):
        self._chars = {c.id: c for c in (chars or [])}
        self.saved = []

    def get_character(self, cid):
        return self._chars.get(cid)

    def save_character(self, c):
        self._chars[c.id] = c
        self.saved.append(c)


def _env(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    lore = _FakeLore([Character(id="c1", name="原卡")])
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    seat = RoomMember(name="分身", character_id="c1")
    s.members.append(seat)
    s.threads.append(Thread(kind="dm", title="分身", member_ids=[seat.id]))
    store.save(s)
    sc = SimpleNamespace(uid="u1", level=0, store=store, lore=lore)
    state = SimpleNamespace(user=lambda _r: sc)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return store, lore, s, seat, request


def test_publish_seat_override_as_new_card(tmp_path):
    store, lore, s, seat, req = _env(tmp_path)
    variant = Character(id="c1", name="分身", intro="副本里改过的简介")
    seat.override = variant.to_dict()
    store.save(s)

    out = publish_member_character(s.id, seat.id, req)
    card = out["character"]

    # 新卡入库：新 id、副本内容保留
    assert card["id"] != "c1"
    assert card["name"] == "分身" and card["intro"] == "副本里改过的简介"
    assert len(lore.saved) == 1 and lore.saved[0].id == card["id"]
    # 座位仍挂原来的副本，原引用不受影响
    s2 = store.get(s.id)
    assert s2.member(seat.id).override["id"] == "c1"
    assert lore.get_character("c1").name == "原卡"


def test_publish_seat_without_card_400(tmp_path):
    store, _lore, s, seat, req = _env(tmp_path)
    seat.character_id = ""
    store.save(s)
    with pytest.raises(HTTPException) as ei:
        publish_member_character(s.id, seat.id, req)
    assert ei.value.status_code == 400


def test_publish_user_seat_400(tmp_path):
    _store, _lore, s, _seat, req = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        publish_member_character(s.id, s.members[0].id, req)
    assert ei.value.status_code == 400


def test_publish_library_card_seat_copies_too(tmp_path):
    """没有副本、直接挂库卡的座位也允许复制入库（按钮只在副本态显示，接口不限制）。"""
    _store, lore, s, seat, req = _env(tmp_path)
    out = publish_member_character(s.id, seat.id, req)
    assert out["character"]["id"] != "c1"
    assert out["character"]["name"] == "原卡"
    assert len(lore.saved) == 1
