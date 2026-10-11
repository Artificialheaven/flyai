"""Galgame 存档点：SavePointStore 快照往返 + API（列表/保存/回档/删除）。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api import routes_galgame
from flyai.galgame.choices import GalgameChoiceManager
from flyai.ids import InvalidName
from flyai.galgame.savepoints import MAX_SAVEPOINTS, SavePointStore
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore, session_to_dict
from flyai.sessions.models import RoomMember, Session, Thread


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _session(sid="sid1"):
    s = Session(id=sid, title="g", mode="galgame")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="凛", character_id="cid1")
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title="凛", member_ids=[m.id]))
    return s


def _env(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s = _session()
    store.save(s)
    sc = SimpleNamespace(uid="u1", level=5, store=store)
    state = SimpleNamespace(
        data_dir=tmp_path / "data",
        galgame_choices=GalgameChoiceManager(),
        galgame_tts_overlay={})
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, s


# ---- Store ----

def test_store_create_and_list(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    rec = sp.create(s.id, session_to_dict(s), "开端")
    assert rec.name == "开端"
    assert rec.messages == 0
    items = sp.list(s.id)
    assert len(items) == 1
    assert items[0].id == rec.id and items[0].file == rec.file
    # 快照文件确实落盘
    assert (tmp_path / "data" / "u1" / "savepoints" / s.id / rec.file).is_file()


def test_store_default_name_and_message_count(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    s.threads[0].messages.append(Message(role="user", content="你好"))
    s.threads[0].messages.append(Message(role="assistant", content="嗨"))
    rec = sp.create(s.id, session_to_dict(s), "")
    assert rec.name.startswith("存档点 · ")
    assert rec.messages == 2


def test_store_snapshot_roundtrip(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    s.galgame = {"background": {"id": "b1", "url": "/u/b1.png"}}
    s.galgame_tts = {"「嗨」": "/u/t1.mp3"}
    rec = sp.create(s.id, session_to_dict(s), "x")
    snap = sp.load_snapshot(s.id, rec.id)
    assert snap["galgame"] == s.galgame
    assert snap["galgame_tts"] == s.galgame_tts
    assert snap["id"] == s.id


def test_store_thumb_extracted_from_scene(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    s.galgame = {"background": {"id": "b1", "url": "/u/b1.png"}}
    rec = sp.create(s.id, session_to_dict(s))
    assert rec.thumb == "/u/b1.png"
    # 无场景时 thumb 为空，不报错
    rec2 = sp.create(s.id, session_to_dict(_session("sid9")))
    assert rec2.thumb == ""


def test_store_cap_evicts_oldest(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    s.galgame = {"background": {"url": "/keep.png"}}
    recs = []
    for n in range(MAX_SAVEPOINTS + 3):
        # 同秒内文件名靠随机 hex 区分
        recs.append(sp.create(s.id, session_to_dict(s), "档%d" % n))
    items = sp.list(s.id)
    assert len(items) == MAX_SAVEPOINTS
    # 最旧的 3 个被淘汰：档3 是幸存者中最旧的
    assert items[0].name == "档3"
    assert sp.load_snapshot(s.id, recs[0].id) is None
    assert not (tmp_path / "data" / "u1" / "savepoints" / s.id
                / recs[0].file).exists()
    # 新档完好
    assert sp.load_snapshot(s.id, recs[-1].id) is not None


def test_store_load_unknown_returns_none(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    assert sp.load_snapshot("sid1", "nope") is None


def test_store_delete(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    s = _session()
    rec = sp.create(s.id, session_to_dict(s))
    fpath = tmp_path / "data" / "u1" / "savepoints" / s.id / rec.file
    assert fpath.is_file()
    assert sp.delete(s.id, rec.id) is True
    assert not fpath.exists()
    assert sp.list(s.id) == []
    assert sp.delete(s.id, rec.id) is False


def test_store_sessions_isolated(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    r1 = sp.create("sid1", session_to_dict(_session("sid1")))
    other = _session("sid2")
    r2 = sp.create("sid2", session_to_dict(other))
    assert [x.id for x in sp.list("sid1")] == [r1.id]
    assert [x.id for x in sp.list("sid2")] == [r2.id]
    assert sp.load_snapshot("sid1", r2.id) is None


def test_store_unsafe_sid_rejected(tmp_path):
    sp = SavePointStore(tmp_path / "data", "u1")
    with pytest.raises(InvalidName):
        sp.create("../escape", {})


# ---- API ----

def test_api_list_empty(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    out = _run(routes_galgame.list_savepoints("sid1", request))
    assert out == {"savepoints": []}


def test_api_create_folds_overlay_only_into_snapshot(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    state.galgame_tts_overlay["sid1"] = {"「嗨」": "/u/t1.mp3"}
    out = _run(routes_galgame.create_savepoint(
        routes_galgame.SavePointBody(session_id="sid1", name="第一次"),
        request))
    rec = out["savepoint"]
    assert rec["name"] == "第一次"
    items = _run(routes_galgame.list_savepoints("sid1", request))["savepoints"]
    assert len(items) == 1 and items[0]["id"] == rec["id"]
    # overlay 进了快照
    store = SavePointStore(state.data_dir, "u1")
    snap = store.load_snapshot("sid1", rec["id"])
    assert snap["galgame_tts"] == {"「嗨」": "/u/t1.mp3"}
    # 会话文件未被回写
    assert sc.store.get("sid1").galgame_tts == {}
    # overlay 保留（存档不消费它）
    assert "sid1" in state.galgame_tts_overlay


def test_api_create_session_404_and_wrong_mode(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.create_savepoint(
            routes_galgame.SavePointBody(session_id="nope"), request))
    assert ei.value.status_code == 404
    normal = Session(id="sid2", mode="normal")
    sc.store.save(normal)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.create_savepoint(
            routes_galgame.SavePointBody(session_id="sid2"), request))
    assert ei.value.status_code == 400


def test_api_restore_replaces_session(tmp_path):
    state, sc, request, s = _env(tmp_path)
    sp_store = SavePointStore(state.data_dir, "u1")
    rec = sp_store.create("sid1", session_to_dict(s), "起点")
    # 存档之后剧情又前进了两条
    cur = sc.store.get("sid1")
    cur.threads[0].messages.append(Message(role="user", content="继续"))
    cur.threads[0].messages.append(Message(role="assistant", content="后来"))
    sc.store.save(cur)

    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: None)
    out = _run(routes_galgame.restore_savepoint(
        routes_galgame.SavePointRestoreBody(
            session_id="sid1", savepoint_id=rec.id),
        request))
    assert out["ok"] is True and out["savepoint"] is None
    restored = sc.store.get("sid1")
    assert len(restored.threads[0].messages) == 0


def test_api_restore_save_current_first(tmp_path):
    state, sc, request, s = _env(tmp_path)
    sp_store = SavePointStore(state.data_dir, "u1")
    rec = sp_store.create("sid1", session_to_dict(s), "起点")
    cur = sc.store.get("sid1")
    cur.threads[0].messages.append(Message(role="assistant", content="后来"))
    sc.store.save(cur)

    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: None)
    out = _run(routes_galgame.restore_savepoint(
        routes_galgame.SavePointRestoreBody(
            session_id="sid1", savepoint_id=rec.id,
            save_current=True, current_name="回档前"),
        request))
    assert out["savepoint"] is not None
    assert out["savepoint"]["name"] == "回档前"
    assert out["savepoint"]["messages"] == 1
    items = sp_store.list("sid1")
    assert len(items) == 2
    # 当前会话已回到旧档
    assert len(sc.store.get("sid1").threads[0].messages) == 0


def test_api_restore_missing_savepoint_404(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: None)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.restore_savepoint(
            routes_galgame.SavePointRestoreBody(
                session_id="sid1", savepoint_id="nope"),
            request))
    assert ei.value.status_code == 404


def test_api_restore_clears_overlay(tmp_path):
    state, sc, request, s = _env(tmp_path)
    sp_store = SavePointStore(state.data_dir, "u1")
    rec = sp_store.create("sid1", session_to_dict(s))
    state.galgame_tts_overlay["sid1"] = {"x": "/u/x"}
    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: None)
    _run(routes_galgame.restore_savepoint(
        routes_galgame.SavePointRestoreBody(
            session_id="sid1", savepoint_id=rec.id),
        request))
    assert "sid1" not in state.galgame_tts_overlay


def test_api_restore_cancels_active_run(tmp_path):
    state, sc, request, s = _env(tmp_path)
    sp_store = SavePointStore(state.data_dir, "u1")
    rec = sp_store.create("sid1", session_to_dict(s))

    async def scenario():
        task = asyncio.create_task(asyncio.sleep(100))
        state.run_manager = SimpleNamespace(
            active_in_session=lambda uid, sid: SimpleNamespace(
                id="run1", task=task))
        await routes_galgame.restore_savepoint(
            routes_galgame.SavePointRestoreBody(
                session_id="sid1", savepoint_id=rec.id),
            request)
        # 运行任务已被取消并收尾
        assert task.cancelled() or task.done()

    _run(scenario())


def test_api_delete_savepoint(tmp_path):
    state, sc, request, s = _env(tmp_path)
    sp_store = SavePointStore(state.data_dir, "u1")
    rec = sp_store.create("sid1", session_to_dict(s))
    out = _run(routes_galgame.delete_savepoint(
        routes_galgame._SavePointDeleteBody(
            session_id="sid1", savepoint_id=rec.id),
        request))
    assert out == {"ok": True}
    assert sp_store.list("sid1") == []
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.delete_savepoint(
            routes_galgame._SavePointDeleteBody(
                session_id="sid1", savepoint_id=rec.id),
            request))
    assert ei.value.status_code == 404
