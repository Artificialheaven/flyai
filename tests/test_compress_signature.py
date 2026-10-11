"""压缩提案 base 签名稳定性回归。

override 是无 id 的精简 dict 时，签名不得因 World/Character 往返重生 uuid
而每次变化，否则 /compress/apply 必然 409。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_session import (
    CompressApplyBody,
    _single_base_signatures,
    session_compress,
    session_compress_apply,
)
from flyai.app import AppState
from flyai.config import AppConfig
from flyai.sessions.resolve import room_base_signatures
from flyai.user import UserScope

SID = "6e074c7af74a4b0fa0a0ae4efc0e1933"


def test_room_signatures_stable_with_idless_overrides():
    session = SimpleNamespace(
        members=[
            SimpleNamespace(id="m1", is_user=False,
                            override={"name": "小雪"}, character_id="", ),
        ],
        world_override={"content": "现代背景", "table": []},
    )
    s1 = room_base_signatures(session, None)
    s2 = room_base_signatures(session, None)
    assert s1 == s2


def test_single_signatures_stable_with_idless_overrides():
    session = SimpleNamespace(
        character_override={"name": "角色"},
        world_override={"content": "世界", "table": []},
    )
    s1 = _single_base_signatures(session, None)
    s2 = _single_base_signatures(session, None)
    assert s1 == s2


def test_room_compress_apply_with_real_idless_world(tmp_path):
    """端到端：磁盘上 world_override 无 id 的真实房间，compress→apply 不得 409。

    AI 归纳用桩替换，全程不落盘。
    """
    import flyai.lore.gen as gen
    import pathlib

    root = pathlib.Path(os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")))

    async def fake_compress_room(_state, _session, _lore):
        return {"members": [], "world": {"content": "测试", "table": []}}

    orig = gen.compress_room
    gen.compress_room = fake_compress_room
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_apply(root, tmp_path))
    finally:
        gen.compress_room = orig


async def _run_apply(root, tmp_path):
    from flyai.user import ADMIN_UID

    state = AppState(config=AppConfig())
    sc = UserScope.build(ADMIN_UID, AppConfig(), root / "data", None)
    sc.store.save = lambda *_a, **_k: None  # 只读复现，禁止落真实磁盘
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    state.user = lambda _r: sc

    resp = await session_compress(SID, request)
    out = await session_compress_apply(
        SID, CompressApplyBody(proposal_id=resp["proposal_id"]), request
    )
    assert out["room"] is True
