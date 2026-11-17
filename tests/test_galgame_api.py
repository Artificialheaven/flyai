"""Galgame API：分句 TTS（门槛/音色/成功）+ 选项应答。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api import routes_galgame, routes_chat
from flyai.config import DeepSeekSettings, RoleplaySettings, TtsSettings
from flyai.errors import DeepSeekError
from flyai.galgame.choices import GalgameChoiceManager
from flyai.lore.models import Character
from flyai.metering import billable_chars, tts_cost
from flyai.schemas import Message, ToolCall
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread

_MP3 = b"ID3FAKE-MP3"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeTtsClient:
    last = None

    def __init__(self, settings):
        self.settings = settings

    async def synthesize(self, text, speaker):
        _FakeTtsClient.last = (text, speaker)
        return SimpleNamespace(audio=_MP3)


def _env(tmp_path, tts=None, level=5, deepseek=None, access=None):
    store = JsonSessionStore(tmp_path / "sessions")
    s = Session(id="sid1", title="g", mode="galgame")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name="凛", character_id="cid1")
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title="凛", member_ids=[m.id]))
    store.save(s)

    chars = {"cid1": Character(id="cid1", name="凛", voice="S_BOUND")}

    class _Lore:
        def get_character(self, cid):
            return chars.get(cid)

    sc = SimpleNamespace(
        uid="u1", level=level, store=store, lore=_Lore(),
        upload_dir=tmp_path / "uploads",
    )
    state = SimpleNamespace(config=SimpleNamespace(
        tts=tts or TtsSettings(
            api_key="k", default_speaker="S_DEFAULT", min_level=0),
        providers=[], deepseek=deepseek, access=access),
        galgame_choices=GalgameChoiceManager(),
        galgame_tts_overlay={})
    # 计费：普通用户路径需要 user_store；测试替身一律放行（金额在专门用例里测）
    state.user_store = SimpleNamespace(
        consume=lambda uid, cost: True,
        refund=lambda uid, cost: None,
        add_used=lambda uid, cost: None,
        get_by_id=lambda uid: None)
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, s


@pytest.fixture
def fake_tts(monkeypatch):
    _FakeTtsClient.last = None
    monkeypatch.setattr(routes_galgame, "DoubaoTtsClient", _FakeTtsClient)


# ---- TTS ----

def test_tts_success_uses_bound_voice(tmp_path, fake_tts):
    state, sc, request, _s = _env(tmp_path)
    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="「你好。」"),
        request))
    assert out["url"].startswith("/uploads/u1/tts_")
    assert out["speaker"] == "S_BOUND" and out["voice_source"] == "character"
    assert _FakeTtsClient.last == ("「你好。」", "S_BOUND")
    assert (sc.upload_dir / out["url"].split("/")[-1]).is_file()


def test_tts_default_voice_when_unbound(tmp_path, fake_tts):
    state, sc, request, s = _env(tmp_path)
    # 角色无绑定音色
    sc.lore.get_character("cid1").voice = ""
    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="嗨"), request))
    assert out["speaker"] == "S_DEFAULT" and out["voice_source"] == "default"


def test_tts_level_below_min_403(tmp_path, fake_tts):
    tts = TtsSettings(api_key="k", default_speaker="d", min_level=10)
    state, sc, request, _s = _env(tmp_path, tts=tts, level=5)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid1", text="x"),
            request))
    assert ei.value.status_code == 403 and "Lv10" in ei.value.detail and "Lv5" in ei.value.detail


def test_tts_no_api_key_400(tmp_path, fake_tts):
    tts = TtsSettings(api_key="", default_speaker="d")
    state, sc, request, _s = _env(tmp_path, tts=tts)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid1", text="x"),
            request))
    assert ei.value.status_code == 400


def test_tts_session_404_and_wrong_mode(tmp_path, fake_tts):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="nope", text="x"),
            request))
    assert ei.value.status_code == 404

    # 普通模式会话
    normal = Session(id="sid2", mode="normal")
    sc.store.save(normal)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid2", text="x"),
            request))
    assert ei.value.status_code == 400


def test_tts_text_validation(tmp_path, fake_tts):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid1", text=" "),
            request))
    assert ei.value.status_code == 400


def test_tts_cached_text_skips_synthesis(tmp_path, fake_tts):
    state, sc, request, s = _env(tmp_path)
    s.galgame_tts = {"「你好。」": "/uploads/u1/tts_old.wav"}
    sc.store.save(s)
    _FakeTtsClient.last = None
    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="「你好。」"),
        request))
    assert out["url"] == "/uploads/u1/tts_old.wav"
    assert out["voice_source"] == "cache"
    assert _FakeTtsClient.last is None  # 未再次合成


def test_tts_new_result_goes_to_overlay_not_direct_save(tmp_path, fake_tts):
    state, sc, request, s = _env(tmp_path)
    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="「嗨」"),
        request))
    # overlay 记录映射，等待 drive 合并落盘（端点不直接 save 以免覆盖）
    assert state.galgame_tts_overlay["sid1"]["「嗨」"] == out["url"]
    on_disk = sc.store.get("sid1")
    assert on_disk.galgame_tts == {}


# ---- 中断工具调用修复（heal）----

def test_heal_inserts_placeholder_for_dangling_call():
    history = [
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id="c1", name="galgame_choice",
                     arguments={"options": ["a", "b"]})]),
        Message(role="user", content="好"),
    ]
    out = routes_chat._heal_dangling_tool_calls(history)
    assert len(out) == 3
    assert out[1].role == "tool" and out[1].tool_call_id == "c1"
    assert "galgame_choice" in out[1].content
    # 入参列表未被改动
    assert len(history) == 2


def test_heal_skips_already_answered_calls():
    history = [
        Message(role="assistant", tool_calls=[
            ToolCall(id="c1", name="galgame_choice")]),
        Message(role="tool", tool_call_id="c1", content="用户选择了：x"),
    ]
    out = routes_chat._heal_dangling_tool_calls(history)
    assert [m.role for m in out] == ["assistant", "tool"]


def test_heal_uses_custom_content_and_fills_all_calls():
    history = [
        Message(role="assistant", tool_calls=[
            ToolCall(id="c1", name="galgame_choice"),
            ToolCall(id="c2", name="galgame_draw_background")]),
    ]
    out = routes_chat._heal_dangling_tool_calls(
        history, custom={"c1": "用户选择了：「甲」"})
    assert out[1].content == "用户选择了：「甲」"
    assert out[2].tool_call_id == "c2" and "作废" in out[2].content


def test_galgame_resume_fills_tool_and_drives(tmp_path, monkeypatch):
    state, sc, request, s = _env(tmp_path)
    thread = s.threads[0]
    state.registry = object()
    state.run_manager = routes_chat.ChatRunManager()

    def fake_resolve_provider(_state, _sc, _pid):
        return SimpleNamespace(cfg=SimpleNamespace(vision=False))

    async def fake_loop(**kwargs):
        yield ("done", {"reason": "stop", "usage": {}})

    monkeypatch.setattr(routes_chat, "_resolve_visible_provider",
                        fake_resolve_provider)
    monkeypatch.setattr(routes_chat, "run_agent_loop", fake_loop)
    monkeypatch.setattr(routes_chat, "visible_image_models",
                        lambda *a, **k: [])
    monkeypatch.setattr(routes_chat, "resolve_attribute_defs",
                        lambda *a, **k: None)
    state.config.tools = SimpleNamespace(
        max_iterations=8,
        model_copy=lambda update=None: SimpleNamespace(max_iterations=40))
    state.config.galgame = SimpleNamespace(max_loops=40)
    state.config.roleplay = RoleplaySettings()

    # 磁盘上：一条带 galgame_choice 但无 tool 响应的 assistant
    thread.messages.append(
        Message(role="assistant", content="「来吧。」", tool_calls=[
            ToolCall(id="call_x1", name="galgame_choice",
                     arguments={"options": ["甲", "乙"]})]))

    req = routes_chat.ChatRequest(
        thread_id=thread.id,
        galgame_resume=routes_chat.GalgameResume(
            tool_call_id="call_x1", text="甲"))

    async def scenario():
        await routes_chat._chat_in_thread(state, sc, s, req)
        run_id = state.run_manager.session_locked(s.id)
        run = state.run_manager.get(run_id, sc.uid)
        await run.task
        return run

    run = _run(scenario())
    roles = [m.role for m in thread.messages]
    assert roles == ["assistant", "tool"]
    tool_msg = thread.messages[1]
    assert tool_msg.tool_call_id == "call_x1"
    assert "甲" in tool_msg.content
    assert run.status == "done"


def test_galgame_resume_unknown_call_400(tmp_path, monkeypatch):
    state, sc, request, s = _env(tmp_path)
    thread = s.threads[0]
    state.run_manager = routes_chat.ChatRunManager()
    thread.messages.append(
        Message(role="assistant", content="x", tool_calls=[
            ToolCall(id="call_x1", name="galgame_choice")]))
    req = routes_chat.ChatRequest(
        thread_id=thread.id,
        galgame_resume=routes_chat.GalgameResume(
            tool_call_id="nope", text="甲"))
    with pytest.raises(HTTPException) as ei:
        _run(routes_chat._chat_in_thread(state, sc, s, req))
    assert ei.value.status_code == 400
    # 未写入任何消息
    assert len(thread.messages) == 1


# ---- Choice ----

def test_choice_resolve_success(tmp_path):
    state, sc, request, _s = _env(tmp_path)

    async def scenario():
        fut = state.galgame_choices.register("run1", "u1")
        out = await routes_galgame.galgame_choice(
            routes_galgame.GalgameChoiceBody(run_id="run1", text="进门"),
            request)
        return out, fut

    out, fut = _run(scenario())
    assert out["ok"] is True
    assert fut.result() == "进门"


def test_choice_unknown_run_404(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_choice(
            routes_galgame.GalgameChoiceBody(run_id="nope", text="x"),
            request))
    assert ei.value.status_code == 404


def test_choice_wrong_owner_403(tmp_path):
    state, sc, request, _s = _env(tmp_path)

    async def scenario():
        state.galgame_choices.register("run1", "someone-else")
        with pytest.raises(HTTPException) as ei:
            await routes_galgame.galgame_choice(
                routes_galgame.GalgameChoiceBody(run_id="run1", text="x"),
                request)
        return ei

    ei = _run(scenario())
    assert ei.value.status_code == 403


# ---- chat() 409 预检（回归：函数内局部 import 曾使 HTTPException
#      成为未绑定局部变量，返回 500 UnboundLocalError）----

def test_chat_active_run_returns_409_not_unbound(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: SimpleNamespace(id="run1"))
    with pytest.raises(HTTPException) as ei:
        _run(routes_chat.chat(
            routes_chat.ChatRequest(session_id="sid1", message="继续"),
            request))
    assert ei.value.status_code == 409


def test_chat_empty_message_existing_session_returns_400(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_chat.chat(
            routes_chat.ChatRequest(session_id="sid1", message="   "),
            request))
    assert ei.value.status_code == 400


# ---- 跨设备活跃 run 发现 ----

def test_active_run_discovery_none(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: None)
    out = _run(routes_chat.chat_active_run("sid1", request))
    assert out == {"run_id": None}


def test_active_run_discovery_returns_id(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    state.run_manager = SimpleNamespace(
        active_in_session=lambda uid, sid: SimpleNamespace(id="runX"))
    out = _run(routes_chat.chat_active_run("sid1", request))
    assert out == {"run_id": "runX"}


def test_choice_empty_body_400(tmp_path):
    state, sc, request, _s = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_choice(
            routes_galgame.GalgameChoiceBody(run_id="run1", text=""),
            request))
    assert ei.value.status_code == 400


# ---- DeepSeek TTS 主路径 / 回退 / 计费 ----

_WAV = b"RIFFFAKE-WAV"


class _FakeDsClient:
    last = None
    fail = False

    def __init__(self, settings):
        self.settings = settings

    async def tts(self, text, voice, fmt):
        _FakeDsClient.last = (text, voice, fmt)
        if _FakeDsClient.fail:
            raise DeepSeekError("upstream down", status_code=502)
        return _WAV


@pytest.fixture
def fake_ds(monkeypatch):
    _FakeDsClient.last = None
    _FakeDsClient.fail = False
    monkeypatch.setattr(routes_galgame, "DeepSeekToolsClient", _FakeDsClient)
    return _FakeDsClient


def _enable_billing(state, sc, tmp_path, total=10.0, used=0.0):
    """换上可核对的用户存储，并启用 usage.jsonl 流水。"""
    users = {"u1": SimpleNamespace(quota_total=total, quota_used=used)}
    charged = []

    class _Store:
        def get_by_id(self, uid):
            return users.get(uid)

        def add_used(self, uid, cost):
            charged.append(cost)
            users.setdefault(
                uid, SimpleNamespace(quota_total=total, quota_used=used))
            users[uid].quota_used += cost

    state.user_store = _Store()
    sc.settings_path = tmp_path / "data" / "u1" / "settings.json"
    return charged


def _ledger(tmp_path):
    path = tmp_path / "data" / "u1" / "usage.jsonl"
    return [json.loads(x)
            for x in path.read_text(encoding="utf-8").splitlines()]


def test_tts_deepseek_success_uses_default_voice_and_bills(
        tmp_path, fake_ds, fake_tts):
    ds = DeepSeekSettings(api_key="sk-ds", tts_voice="tide")
    state, sc, request, _s = _env(tmp_path, deepseek=ds)
    charged = _enable_billing(state, sc, tmp_path)

    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="「你好。」"),
        request))

    # 角色绑定音色不是 stella/tide：使用 DeepSeek 默认音色，输出 wav
    assert fake_ds.last == ("「你好。」", "tide", "wav")
    assert out["speaker"] == "tide" and out["voice_source"] == "default"
    assert out["url"].endswith(".wav")
    assert (sc.upload_dir / out["url"].split("/")[-1]).is_file()
    assert _FakeTtsClient.last is None

    assert charged == [pytest.approx(0.003)]  # 10 计费字符 × 3 元/万字符
    row = _ledger(tmp_path)[0]
    assert row["kind"] == "tts" and row["engine"] == "deepseek"
    assert row["quantity"] == 10 and row["unit"] == "字符"
    assert row["cost"] == pytest.approx(0.003)
    assert row["session_id"] == "sid1"


def test_tts_deepseek_honors_bound_stella_voice(tmp_path, fake_ds, fake_tts):
    ds = DeepSeekSettings(api_key="sk-ds", tts_voice="tide")
    state, sc, request, s = _env(tmp_path, deepseek=ds)
    sc.lore.get_character("cid1").voice = "stella"

    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="嗨"), request))

    assert fake_ds.last == ("嗨", "stella", "wav")
    assert out["speaker"] == "stella" and out["voice_source"] == "character"


def test_tts_deepseek_failure_falls_back_to_doubao_and_bills(
        tmp_path, fake_ds, fake_tts):
    ds = DeepSeekSettings(api_key="sk-ds")
    state, sc, request, _s = _env(tmp_path, deepseek=ds)
    charged = _enable_billing(state, sc, tmp_path)
    fake_ds.fail = True

    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="「你好。」"),
        request))

    assert fake_ds.last is not None
    assert _FakeTtsClient.last == ("「你好。」", "S_BOUND")
    assert out["speaker"] == "S_BOUND"
    assert out["voice_source"] == "character"
    assert out["url"].endswith(".mp3")

    # 回退成功同样按 DeepSeek 字符单价计费
    assert charged == [pytest.approx(0.003)]
    row = _ledger(tmp_path)[0]
    assert row["engine"] == "doubao" and row["kind"] == "tts"
    assert row["quantity"] == 10
    assert row["cost"] == pytest.approx(0.003)


def test_tts_deepseek_failure_without_doubao_key_400(
        tmp_path, fake_ds):
    ds = DeepSeekSettings(api_key="sk-ds")
    tts = TtsSettings(api_key="", default_speaker="d", min_level=0)
    state, sc, request, _s = _env(tmp_path, tts=tts, deepseek=ds)
    charged = _enable_billing(state, sc, tmp_path)
    fake_ds.fail = True

    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid1", text="x"),
            request))
    assert ei.value.status_code == 400
    assert "调用失败" in ei.value.detail
    assert charged == []
    assert not (tmp_path / "data" / "u1" / "usage.jsonl").exists()


def test_tts_engine_doubao_skips_configured_deepseek(
        tmp_path, fake_ds, fake_tts):
    tts = TtsSettings(engine="doubao", api_key="k",
                      default_speaker="S_DEFAULT", min_level=0)
    ds = DeepSeekSettings(api_key="sk-ds")
    state, sc, request, _s = _env(tmp_path, tts=tts, deepseek=ds)
    _enable_billing(state, sc, tmp_path)

    out = _run(routes_galgame.galgame_tts(
        routes_galgame.GalgameTtsBody(session_id="sid1", text="嗨"), request))

    assert fake_ds.last is None
    assert out["speaker"] == "S_BOUND"
    assert _ledger(tmp_path)[0]["engine"] == "doubao"


def test_tts_insufficient_balance_402_before_synthesis(
        tmp_path, fake_ds):
    ds = DeepSeekSettings(api_key="sk-ds")
    state, sc, request, _s = _env(tmp_path, deepseek=ds)
    _enable_billing(state, sc, tmp_path, total=0.0)

    with pytest.raises(HTTPException) as ei:
        _run(routes_galgame.galgame_tts(
            routes_galgame.GalgameTtsBody(session_id="sid1", text="「你好。」"),
            request))
    assert ei.value.status_code == 402
    assert fake_ds.last is None
