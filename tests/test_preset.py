# -*- coding: utf-8 -*-
"""模型预设：酒馆预设解析 / 渲染 / 存储 / API / 参数透传。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.loop import run_agent_loop
from flyai.api.routes_chat import _effective_max_tokens
from flyai.api.routes_preset import (
    ActiveBody,
    PresetBlockPayload,
    PresetPayload,
    clone_preset,
    create_preset,
    delete_preset,
    export_preset,
    import_preset,
    list_presets,
    set_active,
    update_preset,
)
from flyai.config import ProviderConfig, ToolSettings
from flyai.preset.apply import render_preset
from flyai.preset.models import Preset, PresetBlock, sanitize_params
from flyai.preset.store import PresetStore
from flyai.preset.tavern import preset_from_text, preset_to_envelope
from flyai.providers.anthropic import AnthropicProvider
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.providers.openai_compat import OpenAICompatProvider
from flyai.schemas import Message

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PRESET = ROOT / "example" / "【MoM】果实4.96丨喵喵电波@KKM@YUKITEST.txt"


# ---- 示例文件解析 ----

def test_example_preset_stats():
    text = EXAMPLE_PRESET.read_text(encoding="utf-8")
    p, stats = preset_from_text(text, name="果实预设")
    assert p.name == "果实预设"
    assert p.source == "tavern"
    assert stats["blocks"] == 179
    assert stats["enabled"] == 57
    assert stats["skipped"] == 11
    assert stats["main"] == 9
    assert stats["depth"] == 47
    assert stats["tail"] == 1
    assert p.max_tokens == 30000
    assert p.params["temperature"] == 1.2
    assert p.params["top_p"] == 0.94
    assert p.params["top_k"] == 64
    assert p.params["reasoning_effort"] == "high"
    # seed=-1（随机）不进参数
    assert "seed" not in p.params


# ---- 合成酒馆预设：位置映射 / jailbreak 前缀 ----

_SYNTH = {
    "name": "合成",
    "temperature": 0.8,
    "top_p": 0.9,
    "seed": -1,
    "openai_max_tokens": 1234,
    "prompts": [
        {"identifier": "main", "name": "Main", "enabled": True,
         "injection_position": 1, "injection_order": 10, "role": 0,
         "content": "主块 {{char}}"},
        {"identifier": "nsfw", "name": "N", "enabled": True,
         "injection_position": None, "injection_order": 100, "role": 0,
         "content": "NSFW 块"},
        {"identifier": "jailbreak", "name": "J", "enabled": True,
         "injection_position": None, "role": 2, "content": "（继续演出）"},
        {"identifier": "d1", "name": "D", "enabled": True,
         "injection_position": 0, "injection_depth": 2, "injection_order": 5,
         "role": 0, "content": "深处块"},
        {"identifier": "chatHistory", "name": "", "enabled": True,
         "injection_position": None, "role": 0, "content": "  "},
        {"identifier": "enhanceDefinitions", "name": "E", "enabled": False,
         "injection_position": None, "role": 0, "content": "停用块"},
    ],
}


def test_synthetic_tavern_mapping():
    p, stats = preset_from_text(json.dumps(_SYNTH), name="")
    assert p.name == "合成"
    assert p.max_tokens == 1234
    assert p.params == {"temperature": 0.8, "top_p": 0.9}
    assert p.assistant_prefill == "（继续演出）"
    # jailbreak 块本身不进 blocks
    assert all(b.identifier != "jailbreak" for b in p.blocks)
    by_id = {b.identifier: b for b in p.blocks}
    assert by_id["main"].position == "main"
    assert by_id["nsfw"].position == "main"          # null + 主标记
    assert by_id["d1"].position == "depth" and by_id["d1"].depth == 2
    assert by_id["enhanceDefinitions"].enabled is False
    assert stats["blocks"] == 4
    assert stats["enabled"] == 3
    assert stats["main"] == 2
    assert stats["depth"] == 1
    assert stats["tail"] == 0
    assert stats["skipped"] == 1
    assert stats["prefill"] == 1


def test_genamt_fallback_and_top_level_prefill():
    data = {"genamt": 888, "assistant_prefill": "前缀X", "prompts": []}
    p, stats = preset_from_text(json.dumps(data))
    assert p.max_tokens == 888
    assert p.assistant_prefill == "前缀X"
    assert stats["prefill"] == 1


def test_preset_requires_prompts():
    with pytest.raises(ValueError):
        preset_from_text(json.dumps({"temperature": 1}))


def test_flyai_envelope_reimport_resets_id():
    p, _ = preset_from_text(json.dumps(_SYNTH))
    p.id = "oldid"
    env = preset_to_envelope(p)
    assert env["type"] == "flyai-preset"
    p2, stats = preset_from_text(json.dumps(env))
    assert p2.id != "oldid"
    assert p2.name == p.name
    assert len(p2.blocks) == len(p.blocks)
    assert p2.assistant_prefill == p.assistant_prefill


# ---- sanitize_params / 模型 ----

def test_sanitize_params_whitelist_and_clamps():
    out = sanitize_params({
        "temperature": 3, "top_p": 2, "min_p": -1, "top_a": 0.5,
        "frequency_penalty": 5, "presence_penalty": -5,
        "seed": 42, "reasoning_effort": "HIGH", "bogus": 9,
        "top_k": "80", "repetition_penalty": 1.1,
    })
    assert out["temperature"] == 2.0
    assert out["top_p"] == 1.0
    assert out["min_p"] == 0.0
    assert out["frequency_penalty"] == 2.0
    assert out["presence_penalty"] == -2.0
    assert out["seed"] == 42
    assert out["reasoning_effort"] == "high"
    assert out["top_k"] == 80
    assert out["repetition_penalty"] == 1.1
    assert "bogus" not in out


def test_sanitize_params_rejects_bad_values():
    out = sanitize_params({"temperature": "hot", "reasoning_effort": "ultra",
                           "seed": -1, "top_k": None})
    assert out == {}


def test_preset_block_roundtrip_normalizes():
    b = PresetBlock.from_dict({"name": "x", "position": "weird", "role": "ghost",
                               "depth": "3", "insert_order": "12"})
    d = b.to_dict()
    assert d["position"] == "main"
    assert d["role"] == "system"
    assert d["depth"] == 3
    assert d["insert_order"] == 12


# ---- 渲染 ----

def test_render_positions_order_macros_and_prefill():
    p, _ = preset_from_text(json.dumps(_SYNTH))
    r = render_preset(
        p, char_name="猫娘", user_name="主人",
        group_names=["犬娘"], last_message="你好",
    )
    # main 按 injection_order 排序（10 在 100 前）
    lines = r.main_text.split("\n\n")
    assert lines == ["主块 猫娘", "NSFW 块"]
    assert r.tail_text == ""
    assert len(r.depth_blocks) == 1
    assert r.depth_blocks[0]["content"] == "深处块"
    assert r.depth_blocks[0]["depth"] == 2
    assert r.depth_blocks[0]["order"] == 5
    assert r.prefill == "（继续演出）"
    assert r.params == {"temperature": 0.8, "top_p": 0.9}
    assert r.max_tokens == 1234


def test_render_tail_block_and_none_preset():
    p = Preset(name="t", blocks=[
        PresetBlock(name="a", content="尾块", position="tail", insert_order=1),
        PresetBlock(name="b", content="", position="main"),
        PresetBlock(name="c", content="停用", position="main", enabled=False),
    ])
    r = render_preset(p)
    assert r.main_text == ""
    assert r.tail_text == "尾块"
    empty = render_preset(None)
    assert (empty.main_text, empty.tail_text, empty.depth_blocks, empty.prefill) == (
        "", "", [], ""
    )
    assert empty.params == {}


# ---- 存储 ----

def test_preset_store_crud_and_corrupt_skip(tmp_path):
    store = PresetStore(tmp_path / "presets")
    assert store.list() == []
    p = Preset(name="p1")
    store.save(p)
    assert store.get(p.id).name == "p1"
    p2 = Preset(name="p2")
    store.save(p2)
    names = [x.name for x in store.list()]
    assert set(names) == {"p1", "p2"}
    # 损坏文件跳过不炸
    (tmp_path / "presets" / "broken.json").write_text("{不是 json", encoding="utf-8")
    assert len(store.list()) == 2
    assert store.get("broken") is None
    assert store.delete(p.id) is True
    assert store.get(p.id) is None
    assert store.delete(p.id) is False


# ---- API ----

def _env(tmp_path):
    store = PresetStore(tmp_path / "presets")
    settings = SimpleNamespace(active_preset_id="")

    def save():
        saved["v"] = settings.active_preset_id

    saved = {}
    sc = SimpleNamespace(uid="u1", presets=store, user_settings=settings,
                         save_user_settings=save)
    state = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return SimpleNamespace(sc=sc, request=request, store=store, saved=saved)


def _payload(**over):
    base = dict(name="我的预设", note="n",
                params={"temperature": 0.6, "bogus": 1},
                max_tokens=1000, assistant_prefill="前缀",
                blocks=[{"name": "主", "content": "主内容", "position": "main"}])
    base.update(over)
    return PresetPayload(**base)


def test_api_crud_list_active_clone(tmp_path):
    env = _env(tmp_path)
    req = env.request
    assert list_presets(req) == {"presets": [], "active_preset_id": ""}

    created = create_preset(_payload(), req)
    pid = created["id"]
    assert created["params"] == {"temperature": 0.6}  # 白名单过滤
    assert len(created["blocks"]) == 1

    updated = update_preset(pid, _payload(name="改名"), req)
    assert updated["name"] == "改名"
    assert updated["source"] == "flyai"
    assert updated["created_at"] == created["created_at"]

    cloned = clone_preset(pid, req)
    assert cloned["id"] != pid
    assert cloned["name"].endswith("副本")

    assert set_active(ActiveBody(id=pid), req)["active_preset_id"] == pid
    assert env.saved["v"] == pid
    assert list_presets(req)["active_preset_id"] == pid

    # 删除当前启用预设：自动清空启用 id
    assert delete_preset(pid, req) == {"ok": True}
    assert env.sc.user_settings.active_preset_id == ""


def test_api_404_and_bad_id(tmp_path):
    env = _env(tmp_path)
    req = env.request
    with pytest.raises(HTTPException) as e:
        update_preset("missing", _payload(), req)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException):
        set_active(ActiveBody(id="missing"), req)
    with pytest.raises(HTTPException) as e2:
        delete_preset("../escape", req)
    assert e2.value.status_code == 400


def test_api_create_requires_name(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(HTTPException) as e:
        create_preset(_payload(name="  "), env.request)
    assert e.value.status_code == 400


class _Upload:
    def __init__(self, data, filename):
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")
        self.filename = filename

    async def read(self):
        return self._data


def test_api_import_tavern_and_export(tmp_path):
    env = _env(tmp_path)
    file_ = _Upload(json.dumps(_SYNTH), "moemo.txt")
    out = _run(import_preset(env.request, file=file_))
    assert out["stats"]["blocks"] == 4
    p = env.store.get(out["preset"]["id"])
    assert p is not None
    assert p.assistant_prefill == "（继续演出）"

    resp = export_preset(p.id, env.request)
    assert resp.body.decode("utf-8")
    body = json.loads(resp.body)
    assert body["type"] == "flyai-preset"
    assert body["data"]["id"] == p.id


def test_user_scope_builds_preset_store(tmp_path):
    from flyai.config import AppConfig
    from flyai.user import UserScope

    sc = UserScope.build("u1", AppConfig(), tmp_path)
    try:
        p = Preset(name="scope 预设")
        sc.presets.save(p)
        assert sc.presets.get(p.id) is not None
        assert sc.user_settings.active_preset_id == ""
        sc.user_settings.active_preset_id = p.id
        sc.save_user_settings()
        sc2 = UserScope.build("u1", AppConfig(), tmp_path)
        assert sc2.user_settings.active_preset_id == p.id
        assert sc2.presets.get(p.id).name == "scope 预设"
    finally:
        import shutil
        shutil.rmtree(tmp_path / "u1", ignore_errors=True)


def test_api_import_rejects_garbage(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(HTTPException) as e:
        _run(import_preset(env.request, file=_Upload("不是json{", "x.txt")))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        _run(import_preset(
            env.request,
            file=_Upload(json.dumps({"prompts": []}), "x.json"),
        ))
    # prompts 为空且没有任何采样参数 → 无可用块 → 400
    assert e2.value.status_code == 400


# ---- max_tokens 优先级 ----

def test_effective_max_tokens_priority():
    sess = SimpleNamespace(max_tokens=0)
    us = SimpleNamespace(max_tokens=0)
    assert _effective_max_tokens(sess, us, 300) == 300
    us.max_tokens = 200
    assert _effective_max_tokens(sess, us, 300) == 200
    sess.max_tokens = 100
    assert _effective_max_tokens(sess, us, 300) == 100
    assert _effective_max_tokens(SimpleNamespace(max_tokens=0),
                                 SimpleNamespace(max_tokens=0), 0) is None


# ---- loop：extra_params 透传 + prefill 落最后一条 ----

class _FinalProvider:
    def __init__(self):
        self.calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "好")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _NoopRegistry:
    def set_timeout(self, seconds):
        pass


def test_loop_passes_extra_params_and_appends_prefill():
    provider = _FinalProvider()
    history = [Message(role="user", content="在吗")]

    async def go():
        out = []
        async for ev, data in run_agent_loop(
            history, provider, _NoopRegistry(),
            ToolSettings(enabled=False),
            system_prompt="系统",
            extra_params={"temperature": 1.2, "top_k": 64},
            assistant_prefill="（",
            max_tokens=999,
        ):
            out.append(ev)
        return out

    evs = _run(go())
    assert "done" in evs
    messages, kwargs = provider.calls[0]
    assert kwargs["extra_params"] == {"temperature": 1.2, "top_k": 64}
    assert kwargs["max_tokens"] == 999
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "（"
    assert messages[0].role == "system"
    # prefill 不入库
    assert all(m.content != "（" for m in history)


# ---- 聊天路由：启用预设真实注入 ----

def test_chat_thread_applies_active_preset(tmp_path):
    from flyai.agent.runs import ChatRunManager
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.config import RoleplaySettings, ToolSettings
    from flyai.lore.models import Character
    from flyai.sessions.json_store import JsonSessionStore
    from flyai.sessions.models import RoomMember, Session, Thread
    from flyai.tools.registry import ToolRegistry
    from flyai.user import ADMIN_LEVEL, UserSettings

    store = JsonSessionStore(tmp_path / "sessions")
    ch = Character(id="c1", name="角色1")

    s = Session(title="私聊")
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name=ch.name, character_id="c1")
    s.members.append(m)
    dm = Thread(kind="dm", title=m.name, member_ids=[m.id])
    s.threads.append(dm)
    store.save(s)

    class _Lore:
        def get_character(self, cid):
            return ch if cid == "c1" else None

    preset = Preset(
        name="测试预设",
        params={"temperature": 0.5, "top_k": 64},
        max_tokens=1234,
        assistant_prefill="（前缀）",
        blocks=[
            PresetBlock(name="主", content="主块-{{char}}", position="main",
                        insert_order=10),
            PresetBlock(name="深", content="深处块", position="depth", depth=1,
                        insert_order=5),
            PresetBlock(name="尾", content="尾块", position="tail",
                        insert_order=1),
        ],
    )
    presets_store = PresetStore(tmp_path / "presets")
    presets_store.save(preset)
    settings = UserSettings(active_preset_id=preset.id, lore_auto_inject=False)

    sc = SimpleNamespace(
        uid="admin", level=ADMIN_LEVEL, store=store, lore=_Lore(),
        upload_dir=tmp_path / "uploads", presets=presets_store,
        user_settings=settings,
        get_querier=None, save_user_settings=lambda: None,
    )

    captured = {}

    class P:
        async def stream_chat(self, messages, tool_specs, **kwargs):
            if not captured:
                captured["messages"] = list(messages)
                captured["kwargs"] = dict(kwargs)
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "好")

    state = SimpleNamespace(
        config=SimpleNamespace(
            roleplay=RoleplaySettings(format_example=""),
            tools=ToolSettings(enabled=False),
            image_models=[], providers=[],
        ),
        registry=ToolRegistry(),
        run_manager=ChatRunManager(),
        galgame_tts_overlay={},
    )

    req = SimpleNamespace(message="在吗", thread_id=dm.id,
                          target_member_ids=[], shuffle_speakers=False)

    async def body():
        resp = await _chat_in_thread(state, sc, s, req, P())
        async for _frame in resp.body_iterator:
            pass

    _run(body())

    msgs = captured["messages"]
    roles_text = [(m.role, m.content) for m in msgs]
    assert msgs[0].role == "system"
    assert "主块-角色1" in msgs[0].content
    # depth 块插在系统提示词之后、用户消息之前
    assert (msgs[1].role, msgs[1].content) == ("system", "深处块")
    # 尾块（与角色 PHI 同位）在末尾附近，prefill 恒为最后一条
    assert msgs[-1].role == "assistant" and msgs[-1].content == "（前缀）"
    assert any(role == "system" and content == "尾块" for role, content in roles_text)
    # 采样参数与 max_tokens 优先级（预设在无窗口/全局覆盖时生效）
    assert captured["kwargs"]["extra_params"] == {"temperature": 0.5, "top_k": 64}
    assert captured["kwargs"]["max_tokens"] == 1234
    # 预设块不入库
    saved = store.get(s.id)
    contents = [m.content for m in saved.messages]
    assert "深处块" not in contents and "尾块" not in contents and "（前缀）" not in contents


# ---- 提供商请求体 ----

def test_openai_extra_params_override_cfg_but_max_tokens_wins():
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content="data: [DONE]\n\n")

    prov = OpenAICompatProvider(
        ProviderConfig(id="x", type="openai", base_url="http://t/v1", api_key="k",
                       model="m", params={"temperature": 0.7, "repetition_penalty": 1})
    )
    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go():
        async for _ev in prov._stream_chat(
            [Message(role="user", content="hi")], [],
            max_tokens=512,
            extra_params={"temperature": 1.2, "top_p": 0.9, "repetition_penalty": 1.05},
        ):
            pass

    _run(go())
    body = captured["body"]
    assert body["temperature"] == 1.2
    assert body["top_p"] == 0.9
    assert body["repetition_penalty"] == 1.05
    assert body["max_tokens"] == 512


def test_anthropic_extra_params_whitelist():
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        # 极简合法 SSE：仅结束事件，解析器应安静收尾
        return httpx.Response(
            200,
            content=(
                b"event: message_start\n"
                b'data: {"message":{"usage":{"input_tokens":1}}}\n\n'
                b"event: message_delta\n"
                b'data: {"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
            ),
        )

    prov = AnthropicProvider(
        ProviderConfig(id="x", type="anthropic", base_url="http://t", api_key="k",
                       model="m", params={})
    )
    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go():
        async for _ev in prov._stream_chat(
            [Message(role="user", content="hi")], [],
            max_tokens=777,
            extra_params={"temperature": 0.5, "top_p": 0.8, "top_k": 40,
                          "min_p": 0.1, "frequency_penalty": 1},
        ):
            pass

    _run(go())
    body = captured["body"]
    assert body["temperature"] == 0.5
    assert body["top_p"] == 0.8
    assert body["top_k"] == 40
    assert "min_p" not in body
    assert "frequency_penalty" not in body
    assert body["max_tokens"] == 777
