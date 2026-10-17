"""GET /api/my/memory/sources：记忆来源两级聚合（角色卡 · 房间名）。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_memory import memory_sources
from flyai.config import AppConfig
from flyai.lore.models import Character
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.user import UserScope


def _env(tmp_path, uid="u1"):
    scope = UserScope.build(uid, AppConfig(), tmp_path, None)
    state = SimpleNamespace(user=lambda _r: scope)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return scope, request


def _room(store, title, *members):
    s = store.create(title=title)
    s.members = [RoomMember(name="你", is_user=True)] + list(members)
    ai_ids = [m.id for m in members]
    s.threads = [Thread(kind="dm", title="私聊", member_ids=ai_ids)]
    store.save(s)
    return s


def _find(rows, session_title=None, orphan=None, char_id=None):
    out = []
    for r in rows:
        if session_title is not None and r["session_title"] != session_title:
            continue
        if orphan is not None and r["orphan"] != orphan:
            continue
        if char_id is not None and r["char_id"] != char_id:
            continue
        out.append(r)
    return out


def test_sources_empty(tmp_path):
    _scope, req = _env(tmp_path)
    assert memory_sources(req)["sources"] == []


def test_sources_same_card_two_rooms(tmp_path):
    scope, req = _env(tmp_path)
    card = Character(id="TA", name="艾拉")
    scope.lore.save_character(card)
    scope.char_memory.append("TA", "一条事实")

    _room(scope.store, "房间一", RoomMember(name="艾拉", character_id="TA"))
    _room(scope.store, "房间二", RoomMember(name="艾拉", character_id="TA"))

    rows = memory_sources(req)["sources"]
    r1 = _find(rows, session_title="房间一")
    r2 = _find(rows, session_title="房间二")
    assert len(r1) == 1 and len(r2) == 1
    assert r1[0]["char_id"] == "TA" and r1[0]["char_name"] == "艾拉"
    assert r1[0]["active"] == 1 and not r1[0]["orphan"]


def test_source_uses_variant_alias(tmp_path):
    """副本座位：卡名优先显示副本别名（alias）。"""
    scope, req = _env(tmp_path)
    card = Character(id="TA", name="艾拉", alias="温柔版艾拉")
    scope.char_memory.append("TA", "副本里的事实")
    _room(
        scope.store, "副本房",
        RoomMember(name="温柔版艾拉", character_id="TA",
                   override=card.to_dict()),
    )

    rows = memory_sources(req)["sources"]
    assert len(rows) == 1
    assert rows[0]["char_name"] == "温柔版艾拉"


def test_source_memory_char_id_owner(tmp_path):
    """挂载别人的记忆簿时，来源归属 memory_char_id（簿的真正主人）。"""
    scope, req = _env(tmp_path)
    scope.char_memory.append("OTHER", "挂来的簿内容")
    _room(
        scope.store, "挂载房",
        RoomMember(name="分身", character_id="SELF",
                   memory_mode="rw", memory_char_id="OTHER"),
    )

    rows = memory_sources(req)["sources"]
    assert len(rows) == 1
    assert rows[0]["char_id"] == "OTHER"


def test_source_orphan_book(tmp_path):
    """磁盘有簿但没有任何房间引用 → 兜底一行「未挂载」。"""
    scope, req = _env(tmp_path)
    scope.char_memory.append("GONE", "旧卡的记忆")

    rows = memory_sources(req)["sources"]
    assert len(rows) == 1
    assert rows[0]["orphan"] is True
    assert rows[0]["char_id"] == "GONE"
    assert rows[0]["char_name"] == ""


def test_empty_book_not_listed(tmp_path):
    """房间存在、座位存在，但簿里没有任何条目/台账 → 不列来源。"""
    scope, req = _env(tmp_path)
    _room(scope.store, "空房", RoomMember(name="艾拉", character_id="TA"))
    assert memory_sources(req)["sources"] == []
