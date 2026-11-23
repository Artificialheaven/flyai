"""L4-B 会话副本写入：变量 patch/replace、世界书 fork、角色白名单，根卡不动。"""

import copy
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_session import (
    AuthorStateBody,
    CharacterBody,
    StCharacterBody,
    StChatMetadataBody,
    StMessageCreate,
    StMessagePatch,
    StMessagesBody,
    StMvuBody,
    StWorldBody,
    author_state,
    session_set_character,
    st_get_chat_metadata,
    st_patch_messages,
    st_set_chat_metadata,
    st_write_character,
    st_write_mvu,
    st_write_world,
)
from flyai.lore.models import Character, LoreEntry, World
from flyai.sessions.resolve import effective_character
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


class _FakeLore:
    def __init__(self, chars=(), worlds=()):
        self._chars = {c.id: c for c in chars}
        self._worlds = {w.id: w for w in worlds}

    def get_character(self, cid):
        return self._chars.get(cid)

    def get_world(self, wid):
        return self._worlds.get(wid)


def _env(tmp_path, write=True, run_manager=None, chars=(), worlds=()):
    store = JsonSessionStore(tmp_path / "s")
    lore = _FakeLore(list(chars), list(worlds))
    sc = SimpleNamespace(
        uid="u1",
        level=0,
        store=store,
        lore=lore,
        user_settings=SimpleNamespace(st_card_write_enabled=write),
    )
    state = SimpleNamespace()
    state.user = lambda request: sc
    state.run_manager = run_manager
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, lore, store


def _dm(store, char=None):
    """你 + 1 个 AI 的私聊窗口。"""
    s = Session(title="私聊")
    s.members.append(RoomMember(name="你", is_user=True))
    ch = char or Character(id="c1", name="角色1")
    m = RoomMember(name=ch.name, character_id=ch.id)
    s.members.append(m)
    t = Thread(kind="dm", title=m.name, member_ids=[m.id])
    s.threads.append(t)
    store.save(s)
    return s, m, t


def _group(store, n=2):
    s = Session(title="群聊")
    s.members.append(RoomMember(name="你", is_user=True))
    ais = []
    for i in range(n):
        m = RoomMember(name="角色%d" % (i + 1), character_id="c%d" % (i + 1))
        s.members.append(m)
        ais.append(m)
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)
    return s, ais, g


def _append(thread, content, **kw):
    msg = Message(role="assistant", content=content, **kw)
    thread.messages.append(msg)
    return msg


def _body(cls, **kw):
    """绕过 Pydantic 构造期校验，用于测试路由自身的类型检查。"""
    return cls.construct(**kw)


# ---- 公共守卫 ----

def test_write_requires_user_toggle(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path, write=False)
    s, _m, _t = _dm(store)
    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, StMvuBody(mode="patch", ops=[
            {"op": "add", "path": "/hp", "value": 1},
        ]), request)
    assert ei.value.status_code == 403


def test_write_rejects_read_only_session(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    s.mode = "galgame"
    store.save(s)
    with pytest.raises(HTTPException) as ei:
        st_write_world(s.id, StWorldBody(entries=[]), request)
    assert ei.value.status_code == 400
    assert "只读" in ei.value.detail


def test_write_rejects_running_reply(tmp_path):
    runs = ChatRunManager()
    _state, _sc, request, _lore, store = _env(tmp_path, run_manager=runs)
    s, _m, t = _dm(store)
    _append(t, "开头")
    store.save(s)
    runs.create("u1", s.id, t.id)
    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, StMvuBody(mode="patch", ops=[
            {"op": "add", "path": "/hp", "value": 1},
        ]), request)
    assert ei.value.status_code == 409


# ---- 变量写回 ----

def test_mvu_patch_lands_on_last_visible_assistant(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, t = _dm(store)
    target = _append(t, "当前回复", stat_data={"hp": 2, "other": "keep"})
    # 隐藏消息不是可见锚点，即使排在最后也不该被写
    hidden = _append(t, "隐藏", stat_data={"hp": 99}, hidden=True)
    store.save(s)

    out = st_write_mvu(s.id, StMvuBody(mode="patch", ops=[
        {"op": "replace", "path": "/hp", "value": 9},
    ]), request)

    assert out["stat_data"] == {"hp": 9, "other": "keep"}
    s2 = store.get(s.id)
    msgs = {m.id: m for m in s2.thread(t.id).messages}
    assert msgs[target.id].stat_data == {"hp": 9, "other": "keep"}
    assert msgs[hidden.id].stat_data == {"hp": 99}
    assert s2.mvu["stat_data"] == {"hp": 9, "other": "keep"}


def test_mvu_patch_base_falls_back_to_session_state(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, t = _dm(store)
    _append(t, "当前回复")  # 消息自身没有 stat_data
    s.mvu = {"stat_data": {"hp": 1}}
    store.save(s)

    out = st_write_mvu(s.id, StMvuBody(mode="patch", ops=[
        {"op": "add", "path": "/mp", "value": 3},
    ]), request)
    assert out["stat_data"] == {"hp": 1, "mp": 3}
    s2 = store.get(s.id)
    assert s2.thread(t.id).messages[-1].stat_data == {"hp": 1, "mp": 3}
    assert s2.mvu["stat_data"] == {"hp": 1, "mp": 3}


def test_mvu_replace_accepts_object_root(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    _append(s.threads[0], "当前回复", stat_data={"hp": 1})
    store.save(s)

    out = st_write_mvu(s.id, StMvuBody(mode="replace", stat_data={"hp": 5}), request)
    assert out["stat_data"] == {"hp": 5}
    s2 = store.get(s.id)
    assert s2.mvu["stat_data"] == {"hp": 5}


def test_mvu_rejects_bad_mode_and_patch_shape(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    _append(s.threads[0], "当前回复", stat_data={"hp": 1})
    store.save(s)

    cases = [
        (StMvuBody(mode="merge", ops=[]), 400),
        (StMvuBody(mode="patch", ops=[]), 400),
        (_body(StMvuBody, mode="patch", ops="x", stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch", ops=["x"], stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch", ops=[{"op": "merge", "path": "/x"}],
               stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch", ops=[{"op": "add"}],
               stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch",
               ops=[{"op": "add", "path": "/" + "a" * 600}],
               stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch",
               ops=[{"op": "add", "path": "/%d" % i, "value": i}
                    for i in range(201)],
               stat_data=None, thread_id=None), 400),
        (_body(StMvuBody, mode="patch",
               ops=[{"op": "add", "path": "/big", "value": "x" * (150 * 1024)}],
               stat_data=None, thread_id=None), 413),
        (_body(StMvuBody, mode="replace", stat_data=[1],
               ops=None, thread_id=None), 400),
    ]
    for body, code in cases:
        with pytest.raises(HTTPException) as ei:
            st_write_mvu(s.id, body, request)
        assert ei.value.status_code == code, body


def test_mvu_rejects_deep_and_oversize_replace(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    _append(s.threads[0], "当前回复", stat_data={"hp": 1})
    store.save(s)

    nested = {"leaf": 1}
    for _i in range(17):
        nested = {"child": nested}
    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, StMvuBody(mode="replace", stat_data=nested), request)
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, StMvuBody(
            mode="replace", stat_data={"big": "x" * (130 * 1024)},
        ), request)
    assert ei.value.status_code == 413


def test_mvu_requires_dm_and_visible_reply(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    batch = StMvuBody(mode="patch", ops=[{"op": "add", "path": "/hp", "value": 1}])

    s, _ais, g = _group(store)
    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, batch, request)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        st_write_mvu(s.id, StMvuBody(
            mode="patch", ops=[{"op": "add", "path": "/hp", "value": 1}],
            thread_id=g.id,
        ), request)
    assert ei.value.status_code == 400
    assert "私聊" in ei.value.detail

    store2 = JsonSessionStore(tmp_path / "s2")
    s2, _m2, _t2 = _dm(store2)
    state2 = SimpleNamespace(
        user=lambda request: _sc, run_manager=None,
    )
    _sc.store = store2
    request2 = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state2)))
    # 没有可见 AI 楼层时仍写会话级 s.mvu（手机/作者脚本在新聊天里也能落盘）。
    result = st_write_mvu(s2.id, batch, request2)
    assert result["stat_data"]["hp"] == 1
    reloaded = store2.get(s2.id)
    assert (reloaded.mvu or {}).get("stat_data", {}).get("hp") == 1


# ---- 世界书写回 ----

def _world_env(tmp_path):
    world = World(id="w1", name="世界", table=[LoreEntry("原始", "原文")])
    _state, _sc, request, lore, store = _env(tmp_path, worlds=[world])
    s, _m, _t = _dm(store)
    s.world_id = "w1"
    store.save(s)
    return request, lore, store, s


def test_world_write_forks_into_session_copy(tmp_path):
    request, lore, store, s = _world_env(tmp_path)
    before = copy.deepcopy(lore._worlds["w1"].to_dict())

    out = st_write_world(s.id, StWorldBody(
        entries=[{"title": "新条目", "content": "新内容"}],
    ), request)

    assert out["scoped"] is True
    assert out["entries"][0]["title"] == "新条目"
    s2 = store.get(s.id)
    assert s2.world_override["table"][0]["title"] == "新条目"
    assert s2.world_override["name"] == "世界"
    assert s2.world_id == "w1"
    # 根世界书不变
    assert lore._worlds["w1"].to_dict() == before


def test_world_write_replaces_existing_override(tmp_path):
    request, lore, store, s = _world_env(tmp_path)
    st_write_world(s.id, StWorldBody(entries=[{"title": "一", "content": "1"}]), request)
    st_write_world(s.id, StWorldBody(entries=[{"title": "二", "content": "2"}]), request)
    s2 = store.get(s.id)
    assert [e["title"] for e in s2.world_override["table"]] == ["二"]
    assert lore._worlds["w1"].table[0].title == "原始"


def test_world_write_compact_skips_echo(tmp_path):
    request, _lore, store, s = _world_env(tmp_path)
    out = st_write_world(
        s.id,
        StWorldBody(entries=[{"title": "一", "content": "1"}], compact=True),
        request,
    )
    assert out == {"ok": True, "scoped": True}
    assert store.get(s.id).world_override["table"][0]["title"] == "一"


def test_world_write_without_binding_is_400(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    with pytest.raises(HTTPException) as ei:
        st_write_world(s.id, StWorldBody(entries=[{"title": "a", "content": "b"}]), request)
    assert ei.value.status_code == 400
    assert ei.value.detail == "world-not-bound"


def test_world_write_validation(tmp_path):
    request, _lore, store, s = _world_env(tmp_path)
    cases = [
        (_body(StWorldBody, entries={}), 400),
        (StWorldBody(entries=[{"title": str(i)} for i in range(501)]), 400),
        (_body(StWorldBody, entries=[{"title": "a"}, "x"]), 400),
        (StWorldBody(entries=[{"title": "a", "content": "x" * (2 * 1024 * 1024 + 1)}]), 413),
    ]
    for body, code in cases:
        with pytest.raises(HTTPException) as ei:
            st_write_world(s.id, body, request)
        assert ei.value.status_code == code


# ---- 角色副本写回 ----

def test_character_write_whitelist_and_root_untouched(tmp_path):
    char = Character(
        id="c1", name="原名", intro="旧简介", avatar="a.png", public=True,
        regex_scripts=[{"name": "r"}], parent_id="root",
    )
    _state, _sc, request, lore, store = _env(tmp_path, chars=[char])
    s, m, _t = _dm(store, char)
    before = copy.deepcopy(lore._chars["c1"].to_dict())

    out = st_write_character(s.id, StCharacterBody(fields={
        "name": "新名", "intro": "新简介",
    }), request)

    assert out["scoped"] is True
    assert out["character"]["name"] == "新名"
    assert out["character"]["intro"] == "新简介"
    s2 = store.get(s.id)
    seat = [x for x in s2.members if not x.is_user][0]
    assert seat.override["name"] == "新名"
    assert seat.override["intro"] == "新简介"
    assert s2.character_override == seat.override
    # 非白名单字段原样保留（来自副本基线），根卡不变
    assert seat.override["avatar"] == "a.png"
    assert seat.override["parent_id"] == "root"
    assert lore._chars["c1"].to_dict() == before


def test_character_write_compact_skips_echo(tmp_path):
    char = Character(id="c1", name="原名", extensions={"big": "x" * 1000})
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, _m, _t = _dm(store, char)

    out = st_write_character(
        s.id, StCharacterBody(fields={"intro": "新"}, compact=True), request)
    assert out == {"ok": True, "scoped": True}
    s2 = store.get(s.id)
    seat = [x for x in s2.members if not x.is_user][0]
    assert seat.override["intro"] == "新"


def test_character_override_put_keeps_stripped_tavern_helper(tmp_path):
    """响应剥离了 tavern_helper；编辑器回存时服务端要补回，不能丢卡内脚本。"""
    ext = {"tavern_helper": {"scripts": [{"id": "s1", "content": "x"}]}, "world": "w"}
    char = Character(id="c1", name="原名", extensions=copy.deepcopy(ext))
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, m, _t = _dm(store, char)
    m.override = char.to_dict()
    s.character_override = copy.deepcopy(m.override)
    store.save(s)

    payload = char.to_dict()
    payload["extensions"] = {"world": "w"}  # 客户端拿到的剥壳版本
    payload["name"] = "新名"
    session_set_character(s.id, CharacterBody(character=payload), request)

    s2 = store.get(s.id)
    assert s2.character_override["name"] == "新名"
    # 落盘副本不带十几 MB 的脚本，读取时从库主卡补回
    assert "tavern_helper" not in s2.character_override["extensions"]
    eff = effective_character(s2, _lore)
    assert eff.extensions["tavern_helper"]["scripts"][0]["id"] == "s1"


def test_character_write_normalizes_table(tmp_path):
    char = Character(id="c1", name="原名")
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, _m, _t = _dm(store, char)
    st_write_character(s.id, StCharacterBody(fields={
        "table": [{"title": "属性", "content": "hp: 10"}],
        "alternate_greetings": ["你好"],
    }), request)
    s2 = store.get(s.id)
    seat = [x for x in s2.members if not x.is_user][0]
    assert seat.override["table"][0]["title"] == "属性"
    assert seat.override["alternate_greetings"] == ["你好"]


def test_character_write_rejects_non_whitelisted_fields(tmp_path):
    char = Character(id="c1", name="原名")
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, _m, _t = _dm(store, char)

    for key in ("id", "avatar", "public", "parent_id", "regex_scripts",
                "extensions", "voice", "author"):
        with pytest.raises(HTTPException) as ei:
            st_write_character(s.id, StCharacterBody(fields={key: "x"}), request)
        assert ei.value.status_code == 400, key
        assert key in ei.value.detail


def test_character_write_validates_types_and_empty(tmp_path):
    char = Character(id="c1", name="原名")
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, _m, _t = _dm(store, char)

    cases = [
        (StCharacterBody(fields={}), 400),
        (_body(StCharacterBody, fields="name"), 400),
        (StCharacterBody(fields={"name": 1}), 400),
        (StCharacterBody(fields={"alternate_greetings": "你好"}), 400),
        (StCharacterBody(fields={"alternate_greetings": [1]}), 400),
        (StCharacterBody(fields={"table": "x"}), 400),
        (StCharacterBody(fields={"system_prompt": "x" * (2 * 1024 * 1024 + 1)}), 413),
    ]
    for body, code in cases:
        with pytest.raises(HTTPException) as ei:
            st_write_character(s.id, body, request)
        assert ei.value.status_code == code, body


def test_character_write_rejects_group_and_unbound(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _ais, _g = _group(store)
    with pytest.raises(HTTPException) as ei:
        st_write_character(s.id, StCharacterBody(fields={"name": "x"}), request)
    assert ei.value.status_code == 400
    assert "单角色" in ei.value.detail

    store2 = JsonSessionStore(tmp_path / "s2")
    s2 = Session(title="私聊")
    s2.members.append(RoomMember(name="你", is_user=True))
    s2.members.append(RoomMember(name="空"))
    t2 = Thread(kind="dm", title="空", member_ids=[s2.members[1].id])
    s2.threads.append(t2)
    store2.save(s2)
    state2 = SimpleNamespace(user=lambda request: _sc, run_manager=None)
    _sc.store = store2
    _sc.lore = _FakeLore()
    request2 = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state2)))
    with pytest.raises(HTTPException) as ei:
        st_write_character(s2.id, StCharacterBody(fields={"name": "x"}), request2)
    assert ei.value.status_code == 400
    assert "未绑定" in ei.value.detail


def test_author_state_syncs_phone_mainline_and_prompts(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    author_state(
        s.id,
        AuthorStateBody(
            phone_mainline="【手机往来】\n十香：在吗",
            prompts=[
                {"id": "dal_story_anchor", "content": "锚点", "depth": 1},
                {"id": "dal_story_cue", "content": "场记", "depth": 0},
            ],
        ),
        request,
    )
    saved = store.get(s.id)
    author = saved.attributes["author"]
    assert "十香" in author["phone_mainline"]
    assert [item["id"] for item in author["prompts"]] == [
        "dal_story_anchor",
        "dal_story_cue",
    ]
    author_state(
        s.id,
        AuthorStateBody(remove_prompts=["dal_story_cue"]),
        request,
    )
    saved = store.get(s.id)
    assert [item["id"] for item in saved.attributes["author"]["prompts"]] == [
        "dal_story_anchor"
    ]
    # 摘要过长时截断，避免把手机历史无上限写进会话
    author_state(
        s.id,
        AuthorStateBody(phone_mainline="x" * 200000),
        request,
    )
    assert len(store.get(s.id).attributes["author"]["phone_mainline"]) == 131072


# ---- 未绑定世界书时从角色卡派生 ----

def test_world_write_falls_back_to_character_book(tmp_path):
    char = Character(
        id="c1",
        name="角色1",
        table=[LoreEntry(title="旧条目", content="旧内容")],
        extensions={"world": "卡内书"},
    )
    _state, _sc, request, _lore, store = _env(tmp_path, chars=[char])
    s, _m, _t = _dm(store, char=char)
    s.character_id = "c1"
    store.save(s)
    out = st_write_world(
        s.id,
        StWorldBody(entries=[{"title": "新条目", "content": "新内容"}]),
        request,
    )
    assert out["scoped"] is True
    saved = store.get(s.id)
    assert saved.world_override["name"] == "卡内书"
    assert saved.world_override["table"][0]["title"] == "新条目"


# ---- chatMetadata ----

def test_chat_metadata_roundtrip_and_limit(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    assert st_get_chat_metadata(s.id, request) == {"chat_metadata": {}}
    st_set_chat_metadata(
        s.id,
        StChatMetadataBody(metadata={"map": {"x": 1}}),
        request,
    )
    assert st_get_chat_metadata(s.id, request)["chat_metadata"] == {"map": {"x": 1}}
    with pytest.raises(HTTPException) as ei:
        st_set_chat_metadata(
            s.id,
            StChatMetadataBody(metadata={"blob": "x" * (1024 * 1024)}),
            request,
        )
    assert ei.value.status_code == 413


# ---- 消息写回 ----

def test_patch_messages_by_backend_id(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, t = _dm(store)
    first = _append(t, "旧正文", stat_data={"hp": 1})
    store.save(s)
    out = st_patch_messages(
        s.id,
        StMessagesBody(changes=[
            StMessagePatch(
                id=first.id,
                message="新正文",
                data={"stat_data": {"hp": 9}},
                is_hidden=True,
            ),
            StMessagePatch(id="missing", message="忽略"),
        ]),
        request,
    )
    assert out["updated"] == [first.id]
    saved = store.get(s.id)
    msg = saved.threads[0].messages[0]
    assert msg.content == "新正文"
    assert msg.stat_data == {"hp": 9}
    assert msg.hidden is True


def test_patch_messages_extra_roundtrip(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, t = _dm(store)
    first = _append(t, "剧情楼层")
    store.save(s)
    out = st_patch_messages(
        s.id,
        StMessagesBody(changes=[
            StMessagePatch(
                id=first.id,
                extra={"dal_phone_source": {"messageId": "m-a", "replyId": "r-b"}},
            ),
        ]),
        request,
    )
    assert out["updated"] == [first.id]
    saved = store.get(s.id)
    assert saved.threads[0].messages[0].st_extra == {
        "dal_phone_source": {"messageId": "m-a", "replyId": "r-b"}
    }


def test_create_messages_appends_extra_and_variables(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    out = st_patch_messages(
        s.id,
        StMessagesBody(creates=[
            StMessageCreate(
                role="user",
                name="玩家",
                message="0 楼开场正文",
                extra={"dal_opening": {"version": 2, "id": "m1", "mode": "body"}},
                data={"stat_data": {"hp": 3}, "schema": "x", "initialized_lorebooks": {}},
            ),
        ]),
        request,
    )
    assert len(out["created"]) == 1
    assert out["created"][0]["role"] == "user"
    assert out["created"][0]["message_id"] == 0
    assert out["created"][0]["id"]
    # 重新读盘：正文、extra、变量信封里的 stat_data、来源都要保留
    saved = store.get(s.id)
    msg = saved.threads[0].messages[0]
    assert msg.content == "0 楼开场正文"
    assert msg.name == "玩家"
    assert msg.st_extra == {"dal_opening": {"version": 2, "id": "m1", "mode": "body"}}
    assert msg.stat_data == {"hp": 3}
    assert msg.origin == "card"
    # 开场信封要成为会话级当前状态：面板/下一轮提示词立刻可见
    assert saved.mvu["stat_data"] == {"hp": 3}


def test_patch_last_message_data_updates_session_mvu(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, t = _dm(store)
    first = _append(t, "第一条")
    first.stat_data = {"hp": 1}
    second = _append(t, "第二条")
    second.stat_data = {"hp": 2}
    s.mvu = {"stat_data": {"hp": 2}}
    store.save(s)

    # 改历史楼层：只更新该楼层快照，不动会话级状态
    st_patch_messages(
        s.id,
        StMessagesBody(changes=[
            StMessagePatch(id=first.id, data={"stat_data": {"hp": 9}}),
        ]),
        request,
    )
    assert store.get(s.id).mvu["stat_data"] == {"hp": 2}

    # 改最后一条：同步为当前状态
    st_patch_messages(
        s.id,
        StMessagesBody(changes=[
            StMessagePatch(id=second.id, data={"stat_data": {"hp": 5}}),
        ]),
        request,
    )
    assert store.get(s.id).mvu["stat_data"] == {"hp": 5}


def test_create_messages_rejects_bad_extra(tmp_path):
    _state, _sc, request, _lore, store = _env(tmp_path)
    s, _m, _t = _dm(store)
    with pytest.raises(HTTPException) as ei:
        st_patch_messages(
            s.id,
            _body(
                StMessagesBody,
                thread_id=None,
                changes=[],
                creates=[_body(StMessageCreate, role="user", message="x", extra="bad")],
            ),
            request,
        )
    assert ei.value.status_code == 400
