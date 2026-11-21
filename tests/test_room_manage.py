"""房间管理 API：移除成员 / 删除窗口 / 重命名窗口 / 座位设置校验。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_room import (
    MemberUpdate,
    delete_thread,
    remove_member,
    rename_thread,
    update_member,
)
from flyai.lore.character_memory import CharacterMemoryStore
from flyai.lore.models import Character
from flyai.sessions.json_store import JsonSessionStore
from flyai.schemas import Message
from flyai.sessions.models import RoomMember, Session, Thread


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _FakeLore:
    def __init__(self, chars):
        self._chars = {c.id: c for c in chars}

    def get_character(self, cid):
        return self._chars.get(cid)


def _env(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(2):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    ais = list(s.members[1:])
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)

    state = SimpleNamespace()
    state.config = SimpleNamespace(providers=[])
    state.run_manager = ChatRunManager()
    sc = SimpleNamespace(
        uid="admin", level=256, store=store, lore=_FakeLore(chars),
        char_memory=CharacterMemoryStore(tmp_path),
    )
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return state, sc, request, store, s, g, ais


# ---- 移除成员 ----

def test_remove_member_cascades_threads(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    m1_dm = s.threads[0]

    out = remove_member(s.id, ais[0].id, request)
    assert out["removed"] == ais[0].id

    s2 = store.get(s.id)
    assert s2.member(ais[0].id) is None
    # 其专属 dm 窗口删除
    assert s2.thread(m1_dm.id) is None
    # 群窗口保留（历史保留），但成员被摘除
    g2 = s2.thread(g.id)
    assert g2 is not None and ais[0].id not in g2.member_ids


def test_remove_member_who_empties_group_deletes_group(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    # 群里只剩 ais[1]：先把 ais[0] 摘掉再删 ais[1]
    g.member_ids = [ais[1].id]
    store.save(s)

    remove_member(s.id, ais[1].id, request)
    s2 = store.get(s.id)
    assert s2.thread(g.id) is None  # 空群随最后一人删除


def test_remove_member_404_and_user_seat_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, "nope", request)
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, s.members[0].id, request)
    assert ei.value.status_code == 400


def test_remove_member_active_run_409(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    state.run_manager.create(sc.uid, s.id)  # 默认 status=running
    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, ais[0].id, request)
    assert ei.value.status_code == 409


# ---- 删除窗口 ----

def test_delete_thread_ok_and_last_thread_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    out = delete_thread(s.id, g.id, request)
    assert out["removed"] == g.id
    assert store.get(s.id).thread(g.id) is None

    # 再删剩两条 dm；删到最后一条时拒绝
    delete_thread(s.id, s.threads[0].id, request)
    with pytest.raises(HTTPException) as ei:
        delete_thread(s.id, store.get(s.id).threads[0].id, request)
    assert ei.value.status_code == 400


def test_delete_thread_active_run_409(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    state.run_manager.create(sc.uid, s.id)
    with pytest.raises(HTTPException) as ei:
        delete_thread(s.id, g.id, request)
    assert ei.value.status_code == 409


# ---- 重命名窗口 ----

def test_rename_thread_ok_and_empty_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    out = rename_thread(s.id, g.id, SimpleNamespace(title="新群名"), request)
    assert out["title"] == "新群名"
    assert store.get(s.id).thread(g.id).title == "新群名"

    with pytest.raises(HTTPException) as ei:
        rename_thread(s.id, g.id, SimpleNamespace(title="  "), request)
    assert ei.value.status_code == 400


# ---- 座位设置校验 ----

def test_update_member_provider_validation(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    body = MemberUpdate(provider_id="ghost")
    with pytest.raises(HTTPException) as ei:
        update_member(s.id, ais[0].id, body, request)
    assert ei.value.status_code == 403

    # 仅共享且等级达标的模型可选
    state.config.providers = [SimpleNamespace(id="p1", shared=True, min_level=0)]
    body = MemberUpdate(provider_id="p1")
    out = update_member(s.id, ais[0].id, body, request)
    assert out["provider_id"] == "p1"


def test_update_member_memory_mode_validation(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        update_member(s.id, ais[0].id, MemberUpdate(memory_mode="xx"), request)
    assert ei.value.status_code == 400

    # 挂载不存在的记忆簿 → 400
    with pytest.raises(HTTPException) as ei:
        update_member(
            s.id, ais[0].id,
            MemberUpdate(memory_mode="ro", memory_char_id="nobody"),
            request,
        )
    assert ei.value.status_code == 400

    # 记忆簿存在 → 通过
    sc.char_memory.append("c1", "一条旧记忆")
    out = update_member(
        s.id, ais[0].id,
        MemberUpdate(memory_mode="rw", memory_char_id="c1"),
        request,
    )
    assert out["memory_mode"] == "rw" and out["memory_char_id"] == "c1"


# ---- 单聊换卡：AI 座位换卡同步会话顶层挂载 ----

def _single_dm_env(tmp_path, mode="normal"):
    store = JsonSessionStore(tmp_path / "s")
    chars = [Character(id="c1", name="原角色"), Character(id="c2", name="新角色")]
    s = Session(title="单聊", mode=mode)
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="原角色", character_id="c1")
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title="原角色", member_ids=[m.id]))
    store.save(s)
    state = SimpleNamespace()
    state.config = SimpleNamespace(providers=[])
    state.run_manager = ChatRunManager()
    sc = SimpleNamespace(
        uid="admin", level=256, store=store, lore=_FakeLore(chars),
        char_memory=CharacterMemoryStore(tmp_path / "cm"),
    )
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return sc, request, store, s, m


def test_single_dm_switch_card_syncs_top_level(tmp_path):
    sc, request, store, s, m = _single_dm_env(tmp_path)
    out = update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    assert out["character_id"] == "c2" and out["override"] is None
    s2 = store.get(s.id)
    # 座位与顶层会话挂载同步
    assert s2.member(m.id).character_id == "c2"
    assert s2.character_id == "c2" and s2.character_override is None

    # 换成「无角色」：顶层一并清空
    update_member(s.id, m.id, MemberUpdate(character_id=""), request)
    s2 = store.get(s.id)
    assert s2.character_id == "" and s2.member(m.id).character_id == ""


def test_switch_card_replaces_scoped_override(tmp_path):
    sc, request, store, s, m = _single_dm_env(tmp_path)
    m.override = Character(id="x", name="副本").to_dict()
    m.character_id = ""
    s.character_override = m.override
    store.save(s)
    update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    s2 = store.get(s.id)
    assert s2.member(m.id).override is None
    assert s2.character_id == "c2" and s2.character_override is None


def test_switch_card_clears_old_attrs_and_room_memory(tmp_path):
    """换到另一张卡：旧角色的属性值与本房间记忆不能带给新角色（防串人设）；
    但用户显式挂载的跨会话记忆簿配置保留。"""
    sc, request, store, s, m = _single_dm_env(tmp_path)
    m.attributes = {"好感": 80}
    m.memory = "- [2026-10-12] 旧角色的房间记忆"
    m.memory_mode = "rw"
    m.memory_char_id = "c1"
    store.save(s)
    update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    s2 = store.get(s.id)
    m2 = s2.member(m.id)
    assert m2.attributes == {}
    assert m2.memory == ""
    assert m2.memory_mode == "rw" and m2.memory_char_id == "c1"

    # 设置成同一张卡（幂等保存）不应清空
    update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    s2 = store.get(s.id)
    assert s2.member(m.id).memory_mode == "rw"


def test_group_and_galgame_switch_keeps_top_level(tmp_path):
    # 群聊（双 AI）：换座位卡不动顶层挂载
    state, sc, request, store, s, g, ais = _env(tmp_path)
    update_member(s.id, ais[0].id, MemberUpdate(character_id=""), request)
    s2 = store.get(s.id)
    assert s2.character_id == ""  # 顶层本来就空，保持空

    # Galgame 单聊：同样不同步顶层
    sc2, request2, store2, sg, mg = _single_dm_env(tmp_path / "g", mode="galgame")
    sg.character_id = "c1"
    store2.save(sg)
    update_member(sg.id, mg.id, MemberUpdate(character_id="c2"), request2)
    sg2 = store2.get(sg.id)
    assert sg2.member(mg.id).character_id == "c2"
    assert sg2.character_id == "c1"  # Galgame 顶层挂载保持


def test_persona_switch_does_not_touch_top_level(tmp_path):
    # 用户座位（persona）挂卡：从不是会话 AI 挂载，顶层不动
    sc, request, store, s, m = _single_dm_env(tmp_path)
    s.character_id = "c1"
    store.save(s)
    user_seat = s.members[0]
    update_member(s.id, user_seat.id, MemberUpdate(character_id="c2"), request)
    s2 = store.get(s.id)
    assert s2.character_id == "c1"


def test_switch_card_backstamps_old_messages(tmp_path):
    """换卡时旧卡对白补记旧卡 id：自动回忆据此只收新卡自己的历史。"""
    sc, request, store, s, m = _single_dm_env(tmp_path)
    thread = s.threads[0]
    old_msg = Message(role="assistant", content="旧卡台词", actor_id=m.id)
    other_msg = Message(role="assistant", content="别人的台词", actor_id="other")
    user_msg = Message(role="user", content="问")
    thread.messages.extend([user_msg, old_msg, other_msg])
    store.save(s)
    update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    s2 = store.get(s.id)
    msgs = s2.threads[0].messages
    assert msgs[1].character_id == "c1"  # 旧卡对白落到旧卡
    assert msgs[2].character_id is None  # 其他座位不动
    assert msgs[0].character_id is None  # 用户消息不算 AI 归属
    # 已标注的消息不被二次换卡覆盖
    update_member(s.id, m.id, MemberUpdate(character_id="c1"), request)
    s3 = store.get(s.id)
    assert s3.threads[0].messages[1].character_id == "c1"


# ---- Galgame 房间 ----

def _room_create_env(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    chars = [Character(id="c1", name="角色1"), Character(id="c2", name="角色2")]
    sc = SimpleNamespace(
        uid="u1", level=256, store=store, lore=_FakeLore(chars),
        char_memory=CharacterMemoryStore(tmp_path / "cm"),
    )
    state = SimpleNamespace(config=SimpleNamespace(providers=[]))
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return sc, request


def test_create_galgame_room_single_character(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    out = create_room(
        RoomCreate(title="g", character_ids=["c1"], mode="galgame"), request)
    assert out["mode"] == "galgame"
    assert len(out["members"]) == 2  # 用户座 + 1 AI
    assert len(out["threads"]) == 1 and out["threads"][0]["kind"] == "dm"
    # 已落盘
    assert sc.store.get(out["id"]).mode == "galgame"


def test_create_galgame_room_requires_exactly_one_character(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        create_room(RoomCreate(character_ids=[], mode="galgame"), request)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        create_room(RoomCreate(character_ids=["c1", "c2"], mode="galgame"), request)
    assert ei.value.status_code == 400
    # 失败不留空会话文件
    assert sc.store.list() == []


def test_create_galgame_room_forbidden_when_user_override_off(tmp_path):
    """用户覆盖关闭 Galgame → 403（即使全局默认开启）。"""
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    sc.user_settings = SimpleNamespace(galgame_enabled=False)
    with pytest.raises(HTTPException) as ei:
        create_room(
            RoomCreate(character_ids=["c1"], mode="galgame"), request)
    assert ei.value.status_code == 403
    # 被门槛拦下，不落盘任何会话
    assert sc.store.list() == []
    # 普通房间不受影响
    out = create_room(RoomCreate(character_ids=["c1"]), request)
    assert out["mode"] == "normal"


def test_create_galgame_room_forbidden_when_admin_disabled(tmp_path):
    """管理员全局关闭且用户无覆盖 → 403；用户显式覆盖开启 → 允许
    （"全局默认 + 用户可覆盖"语义）。"""
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    request.app.state.flyai.config = SimpleNamespace(
        providers=[], galgame=SimpleNamespace(enabled=False, max_loops=40))
    with pytest.raises(HTTPException) as ei:
        create_room(
            RoomCreate(character_ids=["c1"], mode="galgame"), request)
    assert ei.value.status_code == 403
    # 用户显式覆盖开启 → 允许（"全局默认 + 用户可覆盖"语义）
    sc.user_settings = SimpleNamespace(galgame_enabled=True)
    out = create_room(
        RoomCreate(character_ids=["c1"], mode="galgame"), request)
    assert out["mode"] == "galgame"


def test_create_normal_room_default_mode(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    out = create_room(RoomCreate(character_ids=["c1", "c2"]), request)
    assert out["mode"] == "normal"


# ---- 开场白（first_mes）播种 ----

def test_single_normal_room_seeds_first_mes(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    sc.lore.get_character("c1").first_mes = "  {{char}}看向{{user}}：「来了？」  "
    out = create_room(RoomCreate(character_ids=["c1"]), request)

    msgs = out["threads"][0]["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    # 宏已替换、首尾空白已剥（用户名与提示词侧回退值一致）
    assert msgs[0]["content"] == "角色1看向用户：「来了？」"
    # 已随会话落盘
    saved = sc.store.get(out["id"])
    assert saved.threads[0].messages[0].content == "角色1看向用户：「来了？」"


def test_first_mes_not_seeded_for_multi_character_room(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    sc.lore.get_character("c1").first_mes = "不该出现的开场白"
    out = create_room(RoomCreate(character_ids=["c1", "c2"]), request)
    assert all(t["messages"] == [] for t in out["threads"])


def test_first_mes_not_seeded_for_galgame_room(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    sc.lore.get_character("c1").first_mes = "不该出现的开场白"
    out = create_room(
        RoomCreate(character_ids=["c1"], mode="galgame"), request)
    assert out["threads"][0]["messages"] == []


def test_empty_first_mes_seeds_nothing(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _room_create_env(tmp_path)
    sc.lore.get_character("c1").first_mes = "   \n  "
    out = create_room(RoomCreate(character_ids=["c1"]), request)
    assert out["threads"][0]["messages"] == []


# ---- 开场白选择（alternate_greetings） ----

def _greeting_env(tmp_path):
    sc, request = _room_create_env(tmp_path)
    ch = sc.lore.get_character("c1")
    ch.first_mes = "默认开场"
    ch.alternate_greetings = ["备选一", "备选二"]
    return sc, request


def test_greeting_index_picks_alternate(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    out = create_room(
        RoomCreate(character_ids=["c1"], greeting_index=2), request)
    msgs = out["threads"][0]["messages"]
    assert len(msgs) == 1 and msgs[0]["content"] == "备选二"
    saved = sc.store.get(out["id"])
    assert saved.threads[0].messages[0].content == "备选二"
    # 备用问候选择要持久化，MVU 才能读到其中的内联 <InitVar>
    assert saved.greeting_index == 2


def test_greeting_index_zero_or_missing_uses_first_mes(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    for index in (None, 0):
        out = create_room(
            RoomCreate(character_ids=["c1"], greeting_index=index), request)
        msgs = out["threads"][0]["messages"]
        assert len(msgs) == 1 and msgs[0]["content"] == "默认开场"


def test_greeting_index_out_of_range_falls_back(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    for index in (3, -1):
        out = create_room(
            RoomCreate(character_ids=["c1"], greeting_index=index), request)
        msgs = out["threads"][0]["messages"]
        assert len(msgs) == 1 and msgs[0]["content"] == "默认开场"


def test_greeting_index_blank_alternate_falls_back(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    # 按存储原下标选择：第 2 条为空 → 回落 first_mes，而不是顺延到第 3 条
    sc.lore.get_character("c1").alternate_greetings = ["备选一", "  ", "备选三"]
    out = create_room(
        RoomCreate(character_ids=["c1"], greeting_index=2), request)
    msgs = out["threads"][0]["messages"]
    assert len(msgs) == 1 and msgs[0]["content"] == "默认开场"


def test_greeting_index_substitutes_macros(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    sc.lore.get_character("c1").alternate_greetings = ["  {{char}}对{{user}}说：早  "]
    out = create_room(
        RoomCreate(character_ids=["c1"], greeting_index=1), request)
    msgs = out["threads"][0]["messages"]
    # 无用户 persona，宏回退成“用户”，与提示词侧一致
    assert len(msgs) == 1 and msgs[0]["content"] == "角色1对用户说：早"


def test_greeting_index_ignored_for_multi_character_room(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    out = create_room(
        RoomCreate(character_ids=["c1", "c2"], greeting_index=1), request)
    assert all(t["messages"] == [] for t in out["threads"])


def test_greeting_index_ignored_for_galgame_room(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _greeting_env(tmp_path)
    out = create_room(
        RoomCreate(character_ids=["c1"], mode="galgame", greeting_index=1),
        request)
    assert out["threads"][0]["messages"] == []


# ---- 开局设定（开局*/序章* 世界书条目选择） ----

def _opening_env(tmp_path):
    from flyai.lore.models import LoreEntry

    sc, request = _room_create_env(tmp_path)
    ch = sc.lore.get_character("c1")
    ch.first_mes = "默认开场"
    ch.table = [
        LoreEntry(title="序章", content="序章内容"),
        LoreEntry(title="开局1", content="开局1内容"),
        LoreEntry(title="开局2", content="开局2内容"),
        # 模仿真实卡：只有开局3 默认常驻
        LoreEntry(title="开局3", content="开局3内容", pinned=True),
        LoreEntry(title="其它设定", content="其它内容", pinned=True),
    ]
    return sc, request


def _prompt_card(sc, room):
    from flyai.sessions.resolve import opening_character

    session = sc.store.get(room["id"])
    card = sc.lore.get_character(session.character_id)
    return session, opening_character(session, card)


def test_opening_entry_persisted_and_applied(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _opening_env(tmp_path)
    out = create_room(
        RoomCreate(character_ids=["c1"], opening_entry="开局1"), request)
    assert out["opening_entry"] == "开局1"
    session, card = _prompt_card(sc, out)
    assert session.opening_entry == "开局1"
    titles = {e.title: e for e in card.table}
    assert titles["开局1"].pinned
    # 未选中的开局条目整体移除（只 unpin 仍会被关键字/语义检索命中）
    assert "序章" not in titles and "开局2" not in titles and "开局3" not in titles
    # 非开局条目保持原样
    assert titles["其它设定"].pinned
    # 原卡不被修改：导出/发布仍是作者版本
    original = {e.title: e for e in sc.lore.get_character("c1").table}
    assert not original["开局1"].pinned and original["开局3"].pinned


def test_opening_entry_blank_keeps_card_default(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room
    from flyai.sessions.resolve import opening_character

    sc, request = _opening_env(tmp_path)
    for value in (None, "", "   "):
        out = create_room(
            RoomCreate(character_ids=["c1"], opening_entry=value), request)
        session = sc.store.get(out["id"])
        assert session.opening_entry == ""
        card = sc.lore.get_character("c1")
        assert opening_character(session, card) is card  # 无选择不拷贝
        assert {e.title: e.pinned for e in card.table}["开局3"]


def test_opening_entry_unknown_title_rejected_without_session_file(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _opening_env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        create_room(
            RoomCreate(character_ids=["c1"], opening_entry="开局9"), request)
    assert ei.value.status_code == 400
    assert sc.store.list() == []


def test_opening_entry_ignored_for_multi_character_and_galgame(tmp_path):
    from flyai.api.routes_room import RoomCreate, create_room

    sc, request = _opening_env(tmp_path)
    out = create_room(
        RoomCreate(character_ids=["c1", "c2"], opening_entry="开局1"), request)
    assert sc.store.get(out["id"]).opening_entry == ""
    out = create_room(
        RoomCreate(character_ids=["c1"], mode="galgame", opening_entry="开局1"),
        request)
    assert sc.store.get(out["id"]).opening_entry == ""


def test_switch_card_clears_opening_entry(tmp_path):
    sc, request, store, s, m = _single_dm_env(tmp_path)
    s.opening_entry = "开局1"
    store.save(s)
    # 幂等保存同一张卡：保留选择
    update_member(s.id, m.id, MemberUpdate(character_id="c1"), request)
    assert store.get(s.id).opening_entry == "开局1"
    # 换到另一张卡：旧卡的开局选择清空
    update_member(s.id, m.id, MemberUpdate(character_id="c2"), request)
    assert store.get(s.id).opening_entry == ""
