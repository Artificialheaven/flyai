"""聊天计费端到端（in-process，直接走 _chat_in_thread）。

- 一轮扣 1×模型倍率；余额不足 402、用户消息不落库、锁释放；
- 群聊按发言 AI 逐个累计；跳过的座位退回；
- ProviderError 全额退回；重新生成也扣费；管理员不受限。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_chat import _chat_in_thread
from flyai.billing import quota_view
from flyai.agent.runs import ChatRunManager
from flyai.config import (
    ProviderConfig,
    RoleplaySettings,
    TokenPricing,
    ToolSettings,
)
from flyai.errors import ProviderError
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.registry import ToolRegistry
from flyai.user import ADMIN_UID, UserStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeLore:
    def get_character(self, cid):
        return None


class _Provider:
    """脚本化 provider：默认回「好」；可配跳过/报错/用量上报。cfg 带计费倍率。"""

    def __init__(self, cfg, content="好", fail=False, usage=None):
        self.cfg = cfg
        self._content = content
        self._fail = fail
        self._usage = usage

    async def stream_chat(self, messages, tool_specs, **kwargs):
        if self._fail:
            raise ProviderError("模型接口 500")
        if self._usage:
            yield StreamEvent(StreamEventType.USAGE, usage=dict(self._usage))
        yield StreamEvent(StreamEventType.CONTENT_DELTA, self._content)


def _room(store, n=1):
    """你 + n 个 AI，含 dm 窗口；n>1 时再加 group 窗口。"""
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    members = []
    for i in range(n):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="AI%d" % (i + 1))
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
        members.append(m)
    group = None
    if n > 1:
        group = Thread(kind="group", title="群",
                       member_ids=[m.id for m in members])
        s.threads.append(group)
    store.save(s)
    return s, members, group


def _setup(tmp_path, rates, quota):
    store = JsonSessionStore(tmp_path / "sessions")
    user_store = UserStore(tmp_path)
    u = user_store.create("bob", "", level=0)
    user_store.set_quota_total(u.id, quota)

    cfgs = []
    providers = {}
    contents = {}
    for i, rate in enumerate(rates):
        pid = "p%d" % i
        cfg = ProviderConfig(
            id=pid, base_url="https://example.com", model="m",
            owner="admin", shared=True, min_level=0, rate=rate,
        )
        cfgs.append(cfg)
        providers[pid] = _Provider(cfg, **(contents.get(pid, {})))

    state = SimpleNamespace()
    state.config = SimpleNamespace(
        providers=cfgs,
        active_provider=cfgs[0].id,
        tools=ToolSettings(enabled=False),
        roleplay=RoleplaySettings(format_example=""),
        image_models=[],
    )
    state.registry = ToolRegistry()
    state.run_manager = ChatRunManager()
    state.user_store = user_store
    state.get_provider = lambda pid: providers.get(pid)
    sc = SimpleNamespace(uid=u.id, level=0, store=store, lore=_FakeLore())
    return state, sc, store, u


def _req(thread_id, **kw):
    base = dict(
        message="hi", thread_id=thread_id, provider_id=None,
        target_member_ids=[], shuffle_speakers=False,
        regenerate_message_id=None, edit_message_id=None,
        image_model_id=None, image_engine="", galgame_resume=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _drain(resp):
    async for _frame in resp.body_iterator:
        pass


def test_dm_turn_charges_rate(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[2.0], quota=5)
        s, members, _ = _room(store)
        resp = await _chat_in_thread(
            state, sc, s, _req(s.threads[0].id)
        )
        await _drain(resp)
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(3.0)

    _run(body())


def test_insufficient_quota_402_no_message_and_lock_released(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0], quota=0.5)
        s, members, _ = _room(store)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s, _req(s.threads[0].id)
            )
        assert ei.value.status_code == 402
        # 用户消息没落库
        s2 = store.get(s.id)
        t = s2.thread(s.threads[0].id)
        assert all(m.role != "user" for m in t.messages)
        # 锁已释放、孤儿 run 已终止；余额未变
        assert state.run_manager.session_locked(s.id) is None
        assert state.run_manager.active_in_session(sc.uid, s.id) is None
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(0.5)

    _run(body())


def test_group_charges_per_speaker(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0, 2.0], quota=10)
        s, members, group = _room(store, n=2)
        for i, m in enumerate(members):
            m.provider_id = "p%d" % i
        resp = await _chat_in_thread(
            state, sc, s, _req(group.id)
        )
        await _drain(resp)
        # 两个 AI 各扣 1×rate：10 - 1 - 2
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(7.0)

    _run(body())


def test_skipped_speaker_refunded(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0, 2.0], quota=10)
        s, members, group = _room(store, n=2)
        for i, m in enumerate(members):
            m.provider_id = "p%d" % i
        # 第一位回「略」→ 跳过；把其 provider 换成跳过脚本
        cfg = state.config.providers[0]
        skip_provider = _Provider(cfg, content="略")
        orig_get = state.get_provider
        state.get_provider = lambda pid: (
            skip_provider if pid == cfg.id else orig_get(pid)
        )
        resp = await _chat_in_thread(
            state, sc, s, _req(group.id)
        )
        await _drain(resp)
        # 跳过的座位退回：10 - 2
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(8.0)

    _run(body())


def test_provider_error_full_refund(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.5], quota=4)
        s, members, _ = _room(store)
        cfg = state.config.providers[0]
        state.get_provider = lambda pid: _Provider(cfg, fail=True)
        resp = await _chat_in_thread(
            state, sc, s, _req(s.threads[0].id)
        )
        await _drain(resp)
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(4.0)

    _run(body())


def test_regenerate_charges(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0], quota=3)
        s, members, _ = _room(store)
        from flyai.schemas import Message

        t = s.threads[0]
        user_msg = Message(role="user", content="旧问题")
        t.messages.append(user_msg)
        t.messages.append(Message(role="assistant", content="旧回答"))
        store.save(s)

        # regen 锚点是对应的用户消息
        resp = await _chat_in_thread(
            state, sc, s,
            _req(t.id, message="", regenerate_message_id=user_msg.id),
        )
        await _drain(resp)
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(2.0)

    _run(body())


def test_admin_not_charged(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0], quota=0)
        sc.uid = ADMIN_UID
        sc.level = 256
        s, members, _ = _room(store)
        resp = await _chat_in_thread(
            state, sc, s, _req(s.threads[0].id)
        )
        await _drain(resp)
        # 管理员额度视图为 None；普通用户 bob 的额度未被动用
        assert quota_view(state.user_store, u.id)["used"] == 0

    _run(body())


# ---- 按 token 计费 ----

_USAGE = {"prompt_tokens": 1000, "cached_tokens": 200,
          "completion_tokens": 500}
# 每 1M：命中 0.5、未命中 1、输出 2 → 200*0.5 + 800*1 + 500*2 = 1900 → 0.0019
_USAGE_COST = 0.0019


def _enable_pricing(cfg, **kw):
    prices = {"cache_hit_input": 0.5, "input": 1.0, "output": 2.0}
    prices.update(kw)
    cfg.pricing = TokenPricing(**prices)


def test_priced_dm_settles_actual_usage(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0], quota=1)
        s, members, _ = _room(store)
        cfg = state.config.providers[0]
        _enable_pricing(cfg)
        state.get_provider = lambda pid: _Provider(cfg, usage=_USAGE)
        resp = await _chat_in_thread(
            state, sc, s, _req(s.threads[0].id)
        )
        await _drain(resp)
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(1 - _USAGE_COST)
        assert s.token_usage["cached_tokens"] == 200

    _run(body())


def test_priced_dm_zero_balance_402(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0], quota=0)
        s, members, _ = _room(store)
        cfg = state.config.providers[0]
        _enable_pricing(cfg)
        state.get_provider = lambda pid: _Provider(cfg, usage=_USAGE)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(state, sc, s, _req(s.threads[0].id))
        assert ei.value.status_code == 402
        assert "CDK" in ei.value.detail

    _run(body())


def test_priced_group_settles_each_seat(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0, 1.0], quota=1)
        s, members, group = _room(store, n=2)
        for i, m in enumerate(members):
            m.provider_id = "p%d" % i
            _enable_pricing(state.config.providers[i])
        providers = {
            cfg.id: _Provider(cfg, usage=_USAGE)
            for cfg in state.config.providers
        }
        state.get_provider = lambda pid: providers.get(pid)
        resp = await _chat_in_thread(state, sc, s, _req(group.id))
        await _drain(resp)
        # 两个座位各按实际用量结算，无每轮固定预扣
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(1 - 2 * _USAGE_COST)

    _run(body())


def test_mixed_group_only_rate_seat_precharged(tmp_path):
    async def body():
        state, sc, store, u = _setup(tmp_path, rates=[1.0, 2.0], quota=10)
        s, members, group = _room(store, n=2)
        for i, m in enumerate(members):
            m.provider_id = "p%d" % i
        _enable_pricing(state.config.providers[0])  # 座位 1 按 token 计费
        providers = {
            cfg.id: _Provider(cfg, usage=_USAGE)
            for cfg in state.config.providers
        }
        state.get_provider = lambda pid: providers.get(pid)
        resp = await _chat_in_thread(state, sc, s, _req(group.id))
        await _drain(resp)
        # 只预扣旧费率座位 2，再结算 token 座位 0.0019
        assert quota_view(state.user_store, u.id)["remaining"] == \
            pytest.approx(10 - 2 - _USAGE_COST)

    _run(body())
